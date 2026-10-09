"""Sprint 2.5 review round 1: hostile uploads (parser bombs, oversize, unauthenticated bodies), storage tampering, and the sandbox."""
import hashlib
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from mep.api import uploads
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.ingest.records import IngestRefused
from mep.ingest.sandbox import read_isolated

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL
from tests.rls.test_pg_uploads import ANON, IFC, SUPABASE_URL, post, storage_put

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(ROOT / "rules"), supabase_url=SUPABASE_URL, anon_key=ANON))


def bomb(tmp_path: Path, depth: int = 7, fanout: int = 10) -> bytes:
    """A few kilobytes that ask for fanout ** depth entities: nested block references."""
    import ezdxf
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    block = doc.blocks.new("B0")
    block.add_line((0, 0), (1, 1))
    for i in range(1, depth):
        block = doc.blocks.new(f"B{i}")
        for _ in range(fanout):
            block.add_blockref(f"B{i - 1}", (0, 0))
    doc.modelspace().add_blockref(f"B{depth - 1}", (0, 0))
    path = tmp_path / "bomb.dxf"
    doc.saveas(path)
    return path.read_bytes()


def slow_dxf(tmp_path: Path, polylines: int = 40) -> Path:
    """Within the complexity budget, but every polyline costs about a second in the self-intersection test."""
    import math

    import ezdxf
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    for k in range(polylines):
        pts = [(100 * k + 40 * math.cos(2 * math.pi * i / 1999), 40 * math.sin(2 * math.pi * i / 1999)) for i in range(1999)]
        doc.modelspace().add_lwpolyline(pts, close=True, dxfattribs={"layer": "A-SPACE"})
    path = tmp_path / "slow.dxf"
    doc.saveas(path)
    return path


def test_a_nested_block_bomb_is_refused_quickly_with_a_reason(admin, client, tmp_path):
    data = bomb(tmp_path)
    assert len(data) < 100_000
    f = h.seed(admin)
    started = time.monotonic()
    r = post(client, f, data, name="plan.dxf")
    assert time.monotonic() - started < 30
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_complex"
    assert "nested block references" in r.json()["detail"]["message"]
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0


def test_a_circular_block_reference_is_refused(admin, client, tmp_path):
    import ezdxf
    doc = ezdxf.new("R2018")
    a, b = doc.blocks.new("A"), doc.blocks.new("B")
    a.add_blockref("B", (0, 0))
    b.add_blockref("A", (0, 0))
    doc.modelspace().add_blockref("A", (0, 0))
    path = tmp_path / "loop.dxf"
    doc.saveas(path)
    r = post(client, h.seed(admin), path.read_bytes(), name="loop.dxf")
    assert r.status_code == 422 and "contains itself" in r.json()["detail"]["message"]


def test_the_sandbox_stops_a_reader_that_runs_too_long_or_uses_too_much_cpu(tmp_path):
    path = slow_dxf(tmp_path)
    started = time.monotonic()
    with pytest.raises(IngestRefused, match="took longer"):
        read_isolated("dxf", path, wall_seconds=2)
    with pytest.raises(IngestRefused, match="more processing time or memory"):
        read_isolated("dxf", path, cpu_seconds=1, wall_seconds=60)
    assert time.monotonic() - started < 30


def test_the_sandbox_reads_a_normal_file_and_reports_the_same_result(tmp_path):
    from mep.ingest.ifc import read_ifc
    path = ROOT / "tests/fixtures/ifc/bsi-arch-ifc4.ifc"
    direct, isolated = read_ifc(path), read_isolated("ifc", path)
    assert [(s.key, s.name, s.area_m2, s.centroid_m) for s in isolated.spaces] == \
        [(s.key, s.name, s.area_m2, s.centroid_m) for s in direct.spaces]
    assert isolated.metadata == direct.metadata and isolated.source_sha256 == direct.source_sha256


def test_the_sandbox_reports_a_damaged_file_without_leaking_internals(tmp_path):
    path = tmp_path / "bad.ifc"
    path.write_bytes(b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#1=GARBAGE(((;\nENDSEC;\nEND-ISO-10303-21;\n")
    with pytest.raises(IngestRefused) as e:
        read_isolated("ifc", path)
    assert "Traceback" not in str(e.value) and len(str(e.value)) < 120


# ---- the request body is judged before it is read ----------------------------------------------------------------------

def test_an_unauthenticated_upload_is_refused_before_its_body_is_read(admin, client, monkeypatch):
    f = h.seed(admin)
    big = b"A" * 3_000_000
    r = client.post(f"{h.base(f)}/uploads", files={"file": ("a.ifc", big)})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthenticated"
    r = client.post(f"{h.base(f)}/uploads", headers={"Authorization": "Basic abc"}, files={"file": ("a.ifc", big)})
    assert r.status_code == 401


def test_an_oversized_body_is_refused_by_the_guard_not_the_handler(admin, client, monkeypatch):
    f = h.seed(admin)
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    monkeypatch.setattr(uploads, "BODY_MARGIN", 0)
    r = client.post(f"{h.base(f)}/uploads", headers=h.auth(f["designer"]), files={"file": ("a.ifc", b"B" * 50_000)})
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    # chunked, with no Content-Length: counted as it arrives
    def chunks():
        for _ in range(20):
            yield b"C" * 2_000
    r = client.post(f"{h.base(f)}/uploads", headers={**h.auth(f["designer"]), "Content-Type": "multipart/form-data; boundary=x"},
                    content=chunks())
    assert r.status_code == 413


# ---- the stored file is the evidence ---------------------------------------------------------------------------------

def test_junk_planted_at_a_files_hash_path_is_not_accepted_as_that_file(admin, client):
    f = h.seed(admin)
    sha = hashlib.sha256(IFC).hexdigest()
    assert storage_put(f["designer"], f"{f['firm']}/{f['revision']}/{sha}.ifc", b"planted junk").status_code in (200, 201)
    r = post(client, f, IFC)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "storage_conflict"
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0


def test_the_same_bytes_stored_earlier_are_accepted_as_a_retry(admin, client):
    f = h.seed(admin)
    sha = hashlib.sha256(IFC).hexdigest()
    assert storage_put(f["designer"], f"{f['firm']}/{f['revision']}/{sha}.ifc", IFC).status_code in (200, 201)
    assert post(client, f, IFC).status_code == 200


def test_nothing_can_be_added_to_a_frozen_revision_or_with_another_content_type(admin):
    f = h.seed(admin)
    ok = f"{f['firm']}/{f['revision']}/{'1' * 64}.ifc"
    html = httpx.post(f"{SUPABASE_URL}/storage/v1/object/uploads/{ok}", content=b"<script>x</script>",
                      headers={"apikey": ANON, "Authorization": f"Bearer {h.mint_token(h.SECRET, f['designer'])}",
                               "Content-Type": "text/html"})
    assert html.status_code in (400, 403, 415)
    admin.execute("update revision set frozen_at = now() where id = %s", (f["revision"],))
    assert storage_put(f["designer"], f"{f['firm']}/{f['revision']}/{'2' * 64}.ifc").status_code in (400, 401, 403)


def test_the_public_cannot_create_an_account_on_the_auth_server(admin):
    """`shouldCreateUser: false` in the browser is only a request; the server must refuse sign-up itself."""
    email = f"intruder-{time.time_ns()}@e2e.invalid"
    for path, body in (("signup", {"email": email, "password": "correct horse battery staple"}),
                       ("otp", {"email": email, "create_user": True})):
        r = httpx.post(f"{SUPABASE_URL}/auth/v1/{path}", json=body, headers={"apikey": ANON})
        assert r.status_code in (400, 403, 422) and "access_token" not in r.text, (path, r.status_code, r.text)
