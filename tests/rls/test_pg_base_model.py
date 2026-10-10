"""The architect's model kept for a revision and handed to the ifc-mep job. Needs the local Supabase."""
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.skills_runner import dispatcher
from mep.skills_runner.runner import LocalExecutor

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

ROOT = lin.ROOT
IFC = Path(ROOT) / "tests" / "fixtures" / "ifc" / "bsi-arch-ifc4.ifc"
IFC2X3 = Path(ROOT) / "tests" / "fixtures" / "ifc" / "bsi-arch-ifc2x3.ifc"
SPEC = json.loads((Path(ROOT) / "skills" / "ifc-mep" / "examples" / "ahu_to_terminal" / "spec.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def test_upload_list_download_and_the_guards(admin, client):
    f = h.seed(admin)
    d = h.auth(f["designer"])
    url = f"/revisions/{f['revision']}"
    data = IFC.read_bytes()
    assert client.post(f"{url}/base-model", headers=h.auth(f["checker"]), files={"file": ("a.ifc", data)}).status_code == 403
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("a.ifc", b"nope")}).status_code == 422
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("a.ifc", b"ISO-10303-21;\nHEADER;\nENDSEC;\nEND-ISO-10303-21;\n")}).status_code == 422
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("old.ifc", IFC2X3.read_bytes())}).status_code == 422          # IFC2X3 cannot take these services
    ok = client.post(f"{url}/base-model", headers=d, files={"file": ("../../arch.ifc", data)})
    assert ok.status_code == 200 and ok.json()["file_sha256"] == hashlib.sha256(data).hexdigest() and ok.json()["storeys"][0]["name"] == "00 groundfloor"
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("a.ifc", data)}).status_code == 409
    listed = client.get(f"{url}/base-models", headers=h.auth(f["checker"])).json()["models"]
    assert len(listed) == 1 and listed[0]["file_name"] == "arch.ifc" and "content" not in listed[0]
    mid = listed[0]["id"]
    assert client.get(f"/base-models/{mid}/file", headers=h.auth(f["checker"])).content == data
    other = h.seed(admin)
    assert client.get(f"/base-models/{mid}/file", headers=h.auth(other["designer"])).status_code == 404
    assert client.get(f"{url}/base-models", headers=h.auth(other["designer"])).status_code == 404
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("b.ifc", data + b"\n")}).status_code == 409


def test_ifc_mep_runs_through_the_api_with_the_stored_architect_model(admin, client):
    f = h.seed(admin)
    d = h.auth(f["designer"])
    url = f"/revisions/{f['revision']}"
    assert client.post(f"{url}/base-model", headers=d, files={"file": ("arch.ifc", IFC.read_bytes())}).status_code == 200
    ran = client.post(f"{url}/skills/ifc-mep/run", headers=d, json={"spec": SPEC, "use_firm_defaults": False})
    assert ran.status_code == 200, ran.text
    body = ran.json()
    assert body["status"] == "ok" and body["released"] is True and {x["name"] for x in body["files"]} >= {"GF-MEP.ifc", "manifest.json"}
    assert "architect_model_preserved" in str(body["validation"])
    # a card pointing at a model that was never uploaded is refused, and nothing is released
    other = client.post(f"{url}/skills/ifc-mep/run", headers=d, json={"spec": {**SPEC, "base_ifc_sha256": "0" * 64}, "use_firm_defaults": False})
    assert other.status_code == 200 and other.json()["released"] is False and other.json()["status"] == "spec_rejected"
    # another firm's designer cannot use this firm's model by quoting its checksum
    g = h.seed(admin)
    got = client.post(f"/revisions/{g['revision']}/skills/ifc-mep/run", headers=h.auth(g["designer"]), json={"spec": SPEC, "use_firm_defaults": False})
    assert got.json()["released"] is False and got.json()["status"] == "spec_rejected"


def test_the_worker_dispatcher_hands_the_job_its_architect_model(admin, client):
    import psycopg
    from psycopg.rows import dict_row

    f = h.seed(admin)
    d = h.auth(f["designer"])
    assert client.post(f"/revisions/{f['revision']}/base-model", headers=d, files={"file": ("arch.ifc", IFC.read_bytes())}).status_code == 200
    with psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("insert into skill_job (firm_id, revision_id, requested_by, skill, spec) values (%s, %s, %s, 'ifc-mep', %s::jsonb)",
                     (f["firm"], f["revision"], f["designer"], json.dumps(SPEC)))
        assert dispatcher.serve(DB_URL, LocalExecutor(), once=True) >= 1
        job = conn.execute("select status, result from skill_job where firm_id = %s", (f["firm"],)).fetchone()
    assert job["status"] == "done" and job["result"]["status"] == "ok"
