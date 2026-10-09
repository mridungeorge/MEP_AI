"""/me and /revisions: the role and firm come from the database, and a user lists only their own firm's revisions."""
import time

import jwt
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(h.ROOT / "rules")))


def test_me_reports_the_role_and_firm_from_the_database(admin, client):
    f = h.seed(admin)
    for role in ("designer", "checker"):
        body = client.get("/me", headers=h.auth(f[role])).json()
        assert body["role"] == role and body["firm_id"] == f["firm"] and body["firm_name"] == "pg api test firm"
    forged = jwt.encode({"sub": f["checker"], "aud": "authenticated", "exp": int(time.time()) + 600, "role": "approver",
                         "user_metadata": {"role": "approver"}}, h.SECRET, algorithm="HS256")
    assert client.get("/me", headers={"Authorization": f"Bearer {forged}"}).json()["role"] == "checker"


def test_me_and_revisions_need_a_valid_token(client):
    assert client.get("/me").status_code == 401
    assert client.get("/revisions").status_code == 401
    assert client.get("/revisions", headers={"Authorization": "Bearer nonsense"}).status_code == 401


def test_a_user_lists_only_their_own_firms_revisions(admin, client):
    a, b = h.seed(admin), h.seed(admin)
    mine = client.get("/revisions", headers=h.auth(a["designer"])).json()
    ids = {r["id"] for r in mine}
    assert a["revision"] in ids and b["revision"] not in ids
    row = next(r for r in mine if r["id"] == a["revision"])
    assert row["project_id"] == a["project"] and row["address"] == "1 Test St" and row["ncc_edition"] == "NCC2025"
    assert row["frozen"] is False and row["architect_rev"] == "A" and row["parent_revision_id"] is None
    assert a["revision"] in {r["id"] for r in client.get("/revisions", headers=h.auth(a["checker"])).json()}
