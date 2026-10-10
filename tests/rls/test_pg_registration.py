"""Approver registration: the firm submits, a platform administrator verifies, every step is ledgered. Needs the local Supabase."""
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls import test_pg_review_api as rv
from tests.rls.conftest import DB_URL, uid

PACK = load_pack(lin.ROOT / "rules")
EVIDENCE = "RPEQ register search 12 Oct 2026: name and number match, status current"


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, PACK, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def hdr(u):
    return {"Authorization": f"Bearer {mint_token(h.SECRET, u)}"}


def firm_with_approver(admin):
    f = rv.with_approver(admin, h.seed(admin), registration=None)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    return f


def platform_admin(admin):
    other = h.seed(admin)                                   # the operator's own firm
    admin.execute("insert into platform_admin (user_id, firm_id) values (%s, %s)", (other["designer"], other["firm"]))
    return other["designer"]


def submit(client, f, number="RPEQ 20480", who=None, user=None):
    return client.post("/admin/registrations", headers=hdr(who or f["designer"]),
                       json={"user_id": str(user or f["approver"]), "number": number, "register": "RPEQ", "evidence": EVIDENCE})


def test_the_whole_route_submit_verify_and_the_number_reaches_the_approver_only_then(admin, client):
    f, pa = firm_with_approver(admin), platform_admin(admin)
    r = submit(client, f)
    assert r.status_code == 200 and admin.execute("select registration_no from app_user where id = %s", (f["approver"],)).fetchone()[0] is None
    queue = client.get("/platform/registrations", headers=hdr(pa)).json()
    mine = next(q for q in queue if q["id"] == r.json()["registration_id"])
    assert mine["number"] == "RPEQ 20480" and mine["register"] == "RPEQ"
    assert client.post(f"/platform/registrations/{mine['id']}", headers=hdr(pa), json={"verify": True, "note": "ok"}).status_code == 422       # say what you checked
    done = client.post(f"/platform/registrations/{mine['id']}", headers=hdr(pa), json={"verify": True, "note": "RPEQ register, 12 Oct 2026, name and number match, current"})
    assert done.status_code == 200
    assert admin.execute("select registration_no from app_user where id = %s", (f["approver"],)).fetchone()[0] == "RPEQ 20480"
    ev = admin.execute("select payload from ledger_event where firm_id = %s and kind = 'app_user_changed' and payload ->> 'registration_no' = 'RPEQ 20480'", (f["firm"],)).fetchone()[0]
    assert ev["registration_path"] == "verified"
    kinds = {k for (k,) in admin.execute("select kind from ledger_event where firm_id = %s", (f["firm"],)).fetchall()}
    assert {"registration_submitted", "registration_verified"} <= kinds
    assert client.post(f"/platform/registrations/{mine['id']}", headers=hdr(pa), json={"verify": True, "note": "twice, no way"}).status_code == 422


def test_who_may_submit_and_who_may_decide(admin, client):
    f, pa = firm_with_approver(admin), platform_admin(admin)
    assert submit(client, f, who=f["checker"]).status_code == 403                                  # not an administrator
    assert submit(client, f, user=f["designer"]).status_code == 422                                 # a designer carries no registration
    assert client.post("/admin/registrations", headers=hdr(f["designer"]), json={"user_id": str(f["approver"]), "number": "x", "register": "RPEQ", "evidence": EVIDENCE}).status_code == 422
    assert client.post("/admin/registrations", headers=hdr(f["designer"]), json={"user_id": str(f["approver"]), "number": "RPEQ 1", "register": "STATE", "evidence": EVIDENCE}).status_code == 422
    rid = submit(client, f).json()["registration_id"]
    for who in (f["designer"], f["approver"], f["checker"]):                                          # firm members cannot decide
        assert client.post(f"/platform/registrations/{rid}", headers=hdr(who), json={"verify": True, "note": "I am sure, honestly"}).status_code == 403
        assert client.get("/platform/registrations", headers=hdr(who)).status_code == 403
    # a platform administrator who is also the submitter or the person cannot decide their own
    admin.execute("insert into platform_admin (user_id, firm_id) values (%s, %s) on conflict do nothing", (f["designer"], f["firm"]))
    assert client.post(f"/platform/registrations/{rid}", headers=hdr(f["designer"]), json={"verify": True, "note": "my own firm, I checked it"}).status_code == 422
    rej = client.post(f"/platform/registrations/{rid}", headers=hdr(pa), json={"verify": False, "note": "not on the register"})
    assert rej.status_code == 200
    assert admin.execute("select registration_no, (select status from registration where id = %s) from app_user where id = %s", (rid, f["approver"])).fetchone() == (None, "rejected")


def test_a_new_submission_supersedes_the_old_and_a_correction_replaces_the_number(admin, client):
    f, pa = firm_with_approver(admin), platform_admin(admin)
    first = submit(client, f, "RPEQ 1111").json()["registration_id"]
    second = submit(client, f, "RPEQ 2222").json()["registration_id"]
    assert admin.execute("select status from registration where id = %s", (first,)).fetchone()[0] == "superseded"
    client.post(f"/platform/registrations/{second}", headers=hdr(pa), json={"verify": True, "note": "RPEQ register 12 Oct 2026, current, name matches"})
    third = submit(client, f, "RPEQ 3333").json()["registration_id"]
    client.post(f"/platform/registrations/{third}", headers=hdr(pa), json={"verify": True, "note": "RPEQ register 13 Oct 2026, current, name matches"})
    assert admin.execute("select registration_no from app_user where id = %s", (f["approver"],)).fetchone()[0] == "RPEQ 3333"
    assert [s for (s,) in admin.execute("select status from registration where user_id = %s order by submitted_at", (f["approver"],)).fetchall()] == \
        ["superseded", "superseded", "verified"]


def test_visibility_and_the_overview(admin, client):
    f = firm_with_approver(admin)
    other = firm_with_approver(admin)
    submit(client, f)
    ov = client.get("/admin/overview", headers=hdr(f["designer"])).json()
    assert [r["status"] for r in ov["registrations"]] == ["submitted"]
    assert client.get("/admin/overview", headers=hdr(other["designer"])).json()["registrations"] == []
    assert client.get("/me", headers=hdr(f["designer"])).json()["platform_admin"] is False
    assert client.get("/me", headers=hdr(platform_admin(admin))).json()["platform_admin"] is True


def test_a_direct_change_to_a_registration_number_is_marked_direct_in_the_ledger(admin, client):
    f = firm_with_approver(admin)
    admin.execute("update app_user set registration_no = 'RPEQ 9999' where id = %s", (f["approver"],))
    p = admin.execute("select payload from ledger_event where firm_id = %s and kind = 'app_user_changed' and payload ->> 'registration_no' = 'RPEQ 9999'", (f["firm"],)).fetchone()[0]
    assert p["registration_path"] == "direct"
    assert uid()


def test_an_administrator_cannot_submit_their_own_number_and_the_decider_is_from_another_firm(admin, client):
    f, pa = firm_with_approver(admin), platform_admin(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["approver"],))
    assert submit(client, f, who=f["approver"], user=f["approver"]).status_code == 422          # not for yourself
    rid = submit(client, f).json()["registration_id"]
    # a platform administrator who belongs to the SAME firm may not decide it
    admin.execute("insert into platform_admin (user_id, firm_id) values (%s, %s) on conflict do nothing", (f["checker"], f["firm"]))
    r = client.post(f"/platform/registrations/{rid}", headers=hdr(f["checker"]), json={"verify": True, "note": "my own firm's approver, I checked"})
    assert r.status_code == 422
    ok = client.post(f"/platform/registrations/{rid}", headers=hdr(pa), json={"verify": True, "note": "RPEQ register 12 Oct 2026, name matches, current"})
    assert ok.status_code == 200
    payload = admin.execute("select payload from ledger_event where firm_id = %s and kind = 'registration_verified'", (f["firm"],)).fetchone()[0]
    assert "note" not in payload and len(payload["note_sha256"]) == 64                          # the permanent ledger holds a hash, not the free text
