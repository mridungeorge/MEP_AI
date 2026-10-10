"""The projects dashboard and a project's history. Needs the local Supabase."""
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL, uid


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def hdr(u):
    return {"Authorization": f"Bearer {mint_token(h.SECRET, u)}"}


def add_project(admin, f, address, state="VIC", edition="NCC2025"):
    p, r = uid(), uid()
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition) values (%s, %s, %s, %s, 6, %s)", (p, f["firm"], address, state, edition))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')", (r, f["firm"], p))
    return p, r


def sign(admin, f, rev, gate, user, reg=None):
    admin.execute("insert into signoff (firm_id, revision_id, gate, user_id, registration_no) values (%s, %s, %s, %s, %s)", (f["firm"], rev, gate, user, reg))


def test_the_dashboard_lists_filters_and_reports_where_each_project_stands(admin, client):
    f = h.seed(admin)
    _, r2 = add_project(admin, f, "2 Review Rd", "NSW", "NCC2022")
    _, r3 = add_project(admin, f, "3 Signed Ave", "VIC", "NCC2025")
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id in (%s, %s)", (r2, r3))
    sign(admin, f, r2, "gate1", f["designer"])
    sign(admin, f, r3, "gate1", f["designer"])
    sign(admin, f, r3, "gate2", f["checker"])
    sign(admin, f, r3, "gate3", f["checker"], "RPEQ 20480")
    d = hdr(f["designer"])
    allp = {p["address"]: p for p in client.get("/projects", headers=d).json()}
    assert allp["1 Test St"]["status"] == "drafting" and allp["2 Review Rd"]["status"] == "in_review" and allp["3 Signed Ave"]["status"] == "signed"
    assert allp["3 Signed Ave"]["latest_revision"]["gates_signed"] == ["gate1", "gate2", "gate3"]
    assert [p["address"] for p in client.get("/projects?state=NSW", headers=d).json()] == ["2 Review Rd"]
    assert [p["address"] for p in client.get("/projects?edition=NCC2022", headers=d).json()] == ["2 Review Rd"]
    assert [p["address"] for p in client.get("/projects?status=signed", headers=d).json()] == ["3 Signed Ave"]
    assert [p["address"] for p in client.get("/projects?q=signed", headers=d).json()] == ["3 Signed Ave"]
    assert client.get("/projects?state=XX", headers=d).status_code == 422
    assert client.get("/projects?status=done", headers=d).status_code == 422
    other = h.seed(admin)
    assert [p["address"] for p in client.get("/projects", headers=hdr(other["designer"])).json()] == ["1 Test St"]       # only their own firm's


def test_a_projects_history_shows_revisions_and_who_signed_what(admin, client):
    f = h.seed(admin)
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    sign(admin, f, f["revision"], "gate1", f["designer"])
    sign(admin, f, f["revision"], "gate2", f["checker"])
    hist = client.get(f"/projects/{f['project']}/history", headers=hdr(f["checker"])).json()
    assert hist["project"]["address"] == "1 Test St" and len(hist["revisions"]) == 1
    rev = hist["revisions"][0]
    assert rev["stage"] == "in_review" and [(s["gate"], s["signer"].split("-")[0]) for s in rev["signoffs"]] == [("gate1", "designer"), ("gate2", "checker")]
    assert client.get(f"/projects/{f['project']}/history", headers=hdr(h.seed(admin)["designer"])).status_code == 404
