"""Error reports and logs carry nothing personal; logs are structured; every response has a request id."""
import io
import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mep.observability import JsonFormatter, RequestLogMiddleware, scrub, scrub_event, scrub_text

JWT = "eyJhbGciOiJIUzI1NiJ9" + "a" * 12 + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "b" * 12 + ".sig"


def test_scrubbing_removes_addresses_tokens_and_sensitive_keys():
    assert "jo@firm.example" not in scrub_text("sent to jo@firm.example") and JWT not in scrub_text(f"token {JWT}")
    out = scrub({"Authorization": "Bearer x", "nested": {"email": "a@b.co", "note": "mail a@b.co now", "ok": 3}, "list": [{"api_key": "k"}]})
    assert "Bearer" not in json.dumps(out) and "a@b.co" not in json.dumps(out) and out["nested"]["ok"] == 3


def test_a_sentry_event_loses_request_data_headers_query_user_and_server():
    event = {"request": {"url": "https://x.example/p?token=abc#frag", "headers": {"authorization": "Bearer abc"}, "data": {"password": "p"}, "cookies": {"a": "b"},
                         "query_string": "token=abc"},
             "user": {"id": "u1", "email": "jo@firm.example", "ip_address": "1.2.3.4"}, "server_name": "box", "message": "failed for jo@firm.example",
             "extra": {"jwt": JWT}, "breadcrumbs": {"values": [{"message": "hello jo@firm.example"}]}}
    out = scrub_event(event)
    assert out is not None and out["request"] == {"url": "https://x.example/p"} and out["user"] == {"id": "u1"} and "server_name" not in out
    assert "jo@firm.example" not in json.dumps(out) and JWT not in json.dumps(out)


def test_logs_are_json_with_a_request_id_and_never_the_query_string_or_headers():
    stream = io.StringIO()
    h = logging.StreamHandler(stream)
    h.setFormatter(JsonFormatter())
    log = logging.getLogger("mep.request")
    log.handlers[:] = [h]
    log.setLevel(logging.INFO)
    log.propagate = False
    app = FastAPI()

    @app.get("/thing")
    def thing() -> dict[str, int]:
        return {"n": 1}

    @app.get("/healthz")
    def hz() -> dict[str, str]:
        return {"status": "ok"}

    app.add_middleware(RequestLogMiddleware)
    c = TestClient(app)
    r = c.get("/thing?token=SECRETVALUE", headers={"Authorization": "Bearer SECRETVALUE", "X-Request-Id": "abcdef123456"})
    assert r.headers["x-request-id"] == "abcdef123456"
    assert c.get("/thing", headers={"X-Request-Id": "bad id!"}).headers["x-request-id"] != "bad id!"
    c.get("/healthz")
    lines = [json.loads(x) for x in stream.getvalue().splitlines()]
    assert len(lines) == 2 and lines[0]["request_id"] == "abcdef123456" and lines[0]["path"] == "/thing" and lines[0]["status"] == 200
    assert "SECRETVALUE" not in stream.getvalue() and "token" not in stream.getvalue().lower()
