"""Sprint 2.5: IFC/DXF upload. Type by content, size limit, stored in Supabase Storage under the firm, ingest and health score on
upload, and the bucket's row-level security. Needs the local Supabase (scripts/ci.sh does `supabase db reset`)."""
import hashlib
import os
import uuid
from pathlib import Path

import httpx
import jwt
import psycopg
import pytest
from fastapi.testclient import TestClient
from mep.api import uploads
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL, as_user

ROOT = Path(__file__).resolve().parents[2]
SUPABASE_URL = os.environ.get("MEP_TEST_SUPABASE_URL", "http://127.0.0.1:54321")
ANON = jwt.encode({"iss": "supabase-demo", "role": "anon", "exp": 1983812996}, h.SECRET, algorithm="HS256")
IFC = (ROOT / "tests/fixtures/ifc/bsi-arch-ifc4.ifc").read_bytes()
IFC2 = (ROOT / "tests/fixtures/ifc/bsi-arch-ifc2x3.ifc").read_bytes()


def make_dxf(tmp_path: Path) -> bytes:
    import ezdxf
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    doc.modelspace().add_lwpolyline([(0, 0), (5, 0), (5, 4), (0, 4)], close=True, dxfattribs={"layer": "A-SPACE"})
    path = tmp_path / "plan.dxf"
    doc.saveas(path)
    return path.read_bytes()


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=SUPABASE_URL, anon_key=ANON))


def post(client, f, data, name="model.ifc", role="designer", content_type="application/octet-stream"):
    return client.post(f"{h.base(f)}/uploads", headers=h.auth(f[role]), files={"file": (name, data, content_type)})


def storage_get(f, token_user, path):
    return httpx.get(f"{SUPABASE_URL}/storage/v1/object/uploads/{path}",
                     headers={"apikey": ANON, "Authorization": f"Bearer {h.mint_token(h.SECRET, token_user)}"})


def storage_put(user, path, data=b"x"):
    return httpx.post(f"{SUPABASE_URL}/storage/v1/object/uploads/{path}", content=data,
                      headers={"apikey": ANON, "Authorization": f"Bearer {h.mint_token(h.SECRET, user)}",
                               "Content-Type": "application/octet-stream"})


# ---- the happy paths ----------------------------------------------------------------------------------------------

def test_an_ifc_is_stored_under_the_firm_ingested_and_scored(admin, client):
    f = h.seed(admin)
    r = post(client, f, IFC, name="whatever.txt")                      # the NAME is not what makes it an IFC
    assert r.status_code == 200, r.text
    body = r.json()
    sha = hashlib.sha256(IFC).hexdigest()
    assert body["kind"] == "ifc" and body["sha256"] == sha and body["spaces"] == 2
    assert body["storage_path"] == f"uploads/{f['firm']}/{f['revision']}/{sha}.ifc"
    assert body["health"]["score_percent"] > 0 and "threshold_percent" in body["health"]
    # the stored bytes are the uploaded bytes, readable by the firm, with the ingest run pointing at them
    got = storage_get(f, f["checker"], f"{f['firm']}/{f['revision']}/{sha}.ifc")
    assert got.status_code == 200 and got.content == IFC
    meta = admin.execute("select metadata ->> 'storage_path', source_name, source_kind from ingest_run where revision_id = %s",
                         (f["revision"],)).fetchone()
    assert meta == (body["storage_path"], "whatever.txt", "ifc")
    # the Gate 1 view shows the health score and the spaces as extracted (not confirmed)
    v = client.get(f"{h.base(f)}/gate1", headers=h.auth(f["designer"])).json()
    assert v["health"]["score_percent"] == body["health"]["score_percent"]
    assert {s["provenance"] for s in v["spaces"]} == {"extracted"}


def test_a_dxf_is_recognised_by_content_even_with_an_ifc_name(admin, client, tmp_path):
    f = h.seed(admin)
    r = post(client, f, make_dxf(tmp_path), name="plan.ifc", content_type="model/ifc")
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "dxf" and r.json()["spaces"] == 1 and r.json()["storage_path"].endswith(".dxf")


def test_the_same_file_twice_is_refused(admin, client):
    f = h.seed(admin)
    assert post(client, f, IFC).status_code == 200
    r = post(client, f, IFC, name="again.ifc")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_uploaded"
    assert post(client, f, IFC2).status_code == 200                      # a different file is fine


# ---- type by content, size ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("data, name, why", [
    (b"just some text, not a model\n" * 20, "plan.ifc", "not a recognised"),
    (b"AC1032" + b"\x00" * 200, "plan.dxf", "DWG"),
    (b"PK\x03\x04" + b"x" * 200, "model.ifc", "zipped"),
    (b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('AP214'));\nENDSEC;\n", "model.ifc", "not a recognised"),   # STEP, but not IFC
    (b"", "empty.ifc", "not a recognised"),
])
def test_a_file_that_is_not_ifc_or_dxf_by_content_is_refused(admin, client, data, name, why):
    f = h.seed(admin)
    r = post(client, f, data, name=name)
    assert r.status_code == 415 and why in r.json()["detail"]["message"]
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0


def test_an_oversized_file_is_refused(admin, client, monkeypatch):
    f = h.seed(admin)
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    r = post(client, f, IFC)
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"


def test_a_damaged_file_is_refused_and_leaves_nothing_behind(admin, client):
    f = h.seed(admin)
    damaged = b"ISO-10303-21;\nHEADER;\nFILE_SCHEMA(('IFC4'));\nENDSEC;\nDATA;\n#1=GARBAGE(((;\nENDSEC;\nEND-ISO-10303-21;\n"
    r = post(client, f, damaged)
    assert r.status_code == 422 and r.json()["detail"]["code"] == "unreadable_file"
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    sha = hashlib.sha256(damaged).hexdigest()
    assert storage_get(f, f["designer"], f"{f['firm']}/{f['revision']}/{sha}.ifc").status_code in (400, 404)


# ---- who may upload ------------------------------------------------------------------------------------------------

def test_only_a_designer_of_the_firm_may_upload_to_an_open_revision(admin, client):
    f, other = h.seed(admin), h.seed(admin)
    assert post(client, f, IFC, role="checker").status_code == 403
    assert client.post(f"{h.base(f)}/uploads", files={"file": ("a.ifc", IFC)}).status_code == 401
    r = client.post(f"{h.base(f)}/uploads", headers=h.auth(other["designer"]), files={"file": ("a.ifc", IFC)})
    assert r.status_code == 404                                            # another firm's revision does not exist for them
    admin.execute("update revision set frozen_at = now() where id = %s", (f["revision"],))
    r = post(client, f, IFC)             # a frozen revision is never changed: the new model becomes a CHILD revision
    assert r.status_code == 200, r.text
    child = r.json()["new_revision"]
    assert child["parent_revision_id"] == str(f["revision"]) and child["id"] == r.json()["revision_id"] != str(f["revision"])
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (child["id"],)).fetchone()[0] == 1
    assert admin.execute("select parent_revision_id, status from revision where id = %s", (child["id"],)).fetchone() == (uuid.UUID(str(f["revision"])), "open")
    assert admin.execute("select count(*) from ledger_event where revision_id = %s and kind = 'revision_created'",
                         (child["id"],)).fetchone()[0] == 1
    bad = client.post(f"{h.base(f)}/uploads", headers=h.auth(f["designer"]), files={"file": ("a.ifc", IFC)},
                      data={"architect_rev": "bad/label"})
    assert bad.status_code == 422


def test_without_a_storage_endpoint_the_route_refuses(admin, pack):
    c = TestClient(create_pg_app(DB_URL, h.SECRET, pack))
    f = h.seed(admin)
    assert c.post(f"{h.base(f)}/uploads", headers=h.auth(f["designer"]), files={"file": ("a.ifc", IFC)}).status_code == 503


# ---- row-level security on the bucket itself (the Storage API, as each user) ---------------------------------------

def test_the_bucket_is_private_and_scoped_to_the_firm(admin):
    a, b = h.seed(admin), h.seed(admin)
    sha = "a" * 64
    path_a = f"{a['firm']}/{a['revision']}/{sha}.ifc"
    assert storage_put(a["designer"], path_a).status_code in (200, 201)
    # read: own firm yes (any role), another firm no
    assert storage_get(a, a["checker"], path_a).status_code == 200
    assert storage_get(a, b["designer"], path_a).status_code in (400, 404)
    assert httpx.get(f"{SUPABASE_URL}/storage/v1/object/public/uploads/{path_a}").status_code in (400, 404)    # not public
    # write: not as a checker, not into another firm's folder, not into another firm's revision, not with a stray name
    assert storage_put(a["checker"], f"{a['firm']}/{a['revision']}/{'b' * 64}.ifc").status_code in (400, 401, 403)
    assert storage_put(a["designer"], f"{b['firm']}/{b['revision']}/{'c' * 64}.ifc").status_code in (400, 401, 403)
    assert storage_put(a["designer"], f"{a['firm']}/{b['revision']}/{'d' * 64}.ifc").status_code in (400, 401, 403)
    assert storage_put(a["designer"], f"{a['firm']}/{a['revision']}/evil.exe").status_code in (400, 401, 403)
    assert storage_put(a["designer"], f"{a['firm']}/{a['revision']}/{'e' * 64}.ifc/extra").status_code in (400, 401, 403)
    assert storage_put(uuid.uuid4(), path_a.replace("a" * 64, "f" * 64)).status_code in (400, 401, 403)    # not an app user
    # nobody can overwrite or delete what is stored: it is the evidence the extraction came from
    over = httpx.put(f"{SUPABASE_URL}/storage/v1/object/uploads/{path_a}", content=b"tampered",
                     headers={"apikey": ANON, "Authorization": f"Bearer {h.mint_token(h.SECRET, a['designer'])}"})
    assert over.status_code in (400, 401, 403, 404)
    httpx.request("DELETE", f"{SUPABASE_URL}/storage/v1/object/uploads/{path_a}",
                  headers={"apikey": ANON, "Authorization": f"Bearer {h.mint_token(h.SECRET, a['designer'])}"})
    assert storage_get(a, a["designer"], path_a).content == b"x"


def test_storage_rows_are_invisible_across_firms_in_sql(admin):
    a, b = h.seed(admin), h.seed(admin)
    for f in (a, b):
        assert storage_put(f["designer"], f"{f['firm']}/{f['revision']}/{uuid.uuid4().hex * 2}.dxf").status_code in (200, 201)
    with as_user(a["designer"]) as cur:
        cur.execute("select name from storage.objects where bucket_id = 'uploads'")
        names = {r[0].split("/")[0] for r in cur.fetchall()}
    assert names == {a["firm"]}
    with as_user(a["designer"]) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into storage.objects (bucket_id, name) values ('uploads', %s)",
                    (f"{b['firm']}/{b['revision']}/{'9' * 64}.ifc",))
