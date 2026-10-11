"""UploadGuard decides before FastAPI reads a multipart body: unauthenticated or oversized uploads are refused for EVERY spelling of the
path (round 2 found that the 32-hex, braced and urn:uuid forms, which FastAPI accepts, skipped the guard)."""
import asyncio
import json
import uuid

import pytest
from mep.api import uploads
from mep.api.uploads import UploadGuard

ID = uuid.uuid4()
PATHS = [f"/revisions/{ID}/uploads", f"/revisions/{ID.hex}/uploads", f"/revisions/{{{ID}}}/uploads",
         f"/revisions/urn:uuid:{ID}/uploads", f"/api/revisions/{ID}/uploads", f"/revisions/{ID}/uploads/"]


async def run_guard(path, headers, chunks, method="POST"):
    reached = {"app": False, "reads": 0}

    async def app(scope, receive, send):
        reached["app"] = True
        while True:                                            # the real app reads the whole body
            m = await receive()
            if not m.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    queue = list(chunks)

    async def receive():
        reached["reads"] += 1
        if not queue:
            return {"type": "http.request", "body": b"", "more_body": False}
        body = queue.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(queue)}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path,
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}
    await UploadGuard(app)(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body, reached


@pytest.mark.parametrize("path", PATHS)
def test_an_unauthenticated_upload_is_refused_without_reading_the_body(path):
    status, body, reached = asyncio.run(run_guard(path, {"content-length": "20000000"}, [b"x" * 100]))
    assert status == 401 and json.loads(body)["detail"]["code"] == "unauthenticated"
    assert reached["reads"] == 0 and not reached["app"]


@pytest.mark.parametrize("path", PATHS)
def test_a_declared_oversize_body_is_refused_without_reading_it(path, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(uploads, "BODY_MARGIN", 0)
    status, _body, reached = asyncio.run(run_guard(path, {"authorization": "Bearer t", "content-length": "5000"}, [b"x"]))
    assert status == 413 and reached["reads"] == 0 and not reached["app"]


@pytest.mark.parametrize("path", PATHS)
def test_a_chunked_body_is_cut_at_the_cap_and_the_apps_reply_is_replaced(path, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(uploads, "BODY_MARGIN", 0)
    status, body, reached = asyncio.run(run_guard(path, {"authorization": "Bearer t"}, [b"x" * 600] * 5))
    assert status == 413 and json.loads(body)["detail"]["code"] == "too_large"
    assert reached["reads"] <= 3                      # it stopped feeding the parser soon after the cap


def test_other_requests_pass_through_and_a_small_authenticated_upload_reaches_the_app():
    status, _body, reached = asyncio.run(run_guard(f"/revisions/{ID}/gate1", {}, [b"{}"]))
    assert status == 200 and reached["app"]
    status, body, reached = asyncio.run(run_guard(PATHS[0], {"authorization": "Bearer t", "content-length": "10"}, [b"x" * 10]))
    assert status == 200 and body == b"ok"
    status, _, reached = asyncio.run(run_guard(PATHS[0], {}, [], method="GET"))
    assert status == 200 and reached["app"]


def test_every_route_that_takes_a_file_or_a_raw_body_is_covered_by_the_guard():
    import typing
    from pathlib import Path

    from fastapi import UploadFile
    from fastapi.routing import APIRoute
    from mep.api import uploads
    from mep.api.server import create_pg_app
    from mep.engine.loader import load_pack
    app = create_pg_app("postgresql://x", "s" * 32, load_pack(Path(__file__).resolve().parents[2] / "rules"))
    missing = []
    for r in app.routes:
        if not isinstance(r, APIRoute) or "POST" not in r.methods:
            continue
        hints = typing.get_type_hints(r.endpoint, include_extras=True)
        takes_file = any("UploadFile" in repr(h) or h is UploadFile for h in hints.values()) or "Request" in {getattr(h, "__name__", "") for h in hints.values()}
        if takes_file and uploads._route_limit(r.path) is None:
            missing.append(r.path)
    assert missing == []
