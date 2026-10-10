"""E-mail notifications: who is told what (database triggers), preferences, and the sender with a fake provider. Needs the local Supabase."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.notifications import ResendMailer, process_outbox
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls import test_pg_review_api as rv
from tests.rls.conftest import DB_URL, uid

PACK = load_pack(lin.ROOT / "rules")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, PACK, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def hdr(u):
    return {"Authorization": f"Bearer {mint_token(h.SECRET, u)}"}


def fake_mailer(sent, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        sent.append({"auth": request.headers["authorization"], **body})
        return httpx.Response(status, json={"id": "x"})
    return ResendMailer("re_test_key", "MEP <n@example.invalid>", "https://app.example.invalid", client=httpx.Client(transport=httpx.MockTransport(handler)))


def outbox(admin, f):
    return admin.execute("select u.email, n.kind, n.status, n.subject, n.body, n.link_path from notification n join app_user u on u.id = n.user_id"
                         " where n.firm_id = %s order by n.id", (f["firm"],)).fetchall()


def firm(admin):
    f = rv.with_approver(admin, h.seed(admin))
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    return f


def sign(admin, f, rev, gate, user):
    admin.execute("insert into signoff (firm_id, revision_id, gate, user_id, registration_no) values (%s, %s, %s, %s, %s)",
                  (f["firm"], rev, gate, user, "RPEQ 12345" if gate == "gate3" else None))


def test_the_next_person_in_the_chain_is_asked_and_the_starters_hear_of_each_signature(admin):
    f = firm(admin)
    sign(admin, f, f["revision"], "gate1", f["designer"])
    rows = outbox(admin, f)
    kinds = {(e.split("-")[0], k) for e, k, *_ in rows}
    assert ("checker", "review_requested") in kinds and ("designer", "review_requested") not in kinds and ("approver", "review_requested") not in kinds
    sign(admin, f, f["revision"], "gate2", f["checker"])
    kinds = {(e.split("-")[0], k) for e, k, *_ in outbox(admin, f)}
    assert ("approver", "review_requested") in kinds and ("designer", "signed") not in {(e.split("-")[0], k) for e, k, *_ in outbox(admin, f)[:1]}
    assert ("designer", "signed") in kinds                                                    # the designer (who also administers) is told gate 2 was signed
    for e, k, status, subject, body, link in outbox(admin, f):
        assert status == "pending" and link.startswith("/projects/") and "Rev A" in subject
        assert "PASS" not in body and "FAIL" not in body                                      # no results in an e-mail


def test_preferences_switch_a_kind_off_per_person(admin, client):
    f = firm(admin)
    assert client.put("/me/notifications", headers=hdr(f["checker"]), json={"review_requested": False}).status_code == 200
    assert client.get("/me/notifications", headers=hdr(f["checker"])).json()["kinds"]["review_requested"] is False
    assert client.get("/me/notifications", headers=hdr(f["designer"])).json()["kinds"]["review_requested"] is True
    sign(admin, f, f["revision"], "gate1", f["designer"])
    assert not [r for r in outbox(admin, f) if r[0].startswith("checker") and r[1] == "review_requested"]
    assert client.put("/me/notifications", headers=hdr(f["checker"]), json={"bogus": True}).status_code == 422
    admin.execute("update app_user set active = false where id = %s", (f["approver"],))
    sign(admin, f, f["revision"], "gate2", f["checker"])
    assert not [r for r in outbox(admin, f) if r[0].startswith("approver")]                  # a deactivated person is not written to


def test_changes_requested_reaches_the_designer_who_froze_it(admin, client):
    f = rv.frozen(admin, client, PACK)
    admin.execute("delete from notification where firm_id = %s", (f["firm"],))
    ws = client.get(f"/revisions/{f['revision']}/review", headers=hdr(f["checker"])).json()
    one = ws["results"][0]
    r = client.post(f"/revisions/{f['revision']}/review/decisions", headers=hdr(f["checker"]),
                    json={"result_id": one["id"], "decision": "request_changes", "reason": "please recheck the airflow figure"})
    assert r.status_code == 200, r.text
    got = [x for x in outbox(admin, f) if x[1] == "changes_requested"]
    assert len(got) == 1 and got[0][0].startswith("designer") and "Changes requested" in got[0][3]


def test_the_share_link_creator_hears_when_it_is_opened_at_most_hourly(admin):
    f = firm(admin)
    tok = "a1" * 32
    admin.execute("insert into ledger_link (token, firm_id, revision_id, expires_at, created_by) values (%s, %s, %s, now() + interval '1 day', %s)",
                  (tok, f["firm"], f["revision"], f["designer"]))
    admin.execute("update ledger_link set views = views + 1, last_viewed_at = now() where token = %s", (tok,))
    admin.execute("update ledger_link set views = views + 1, last_viewed_at = now() where token = %s", (tok,))
    assert len([r for r in outbox(admin, f) if r[1] == "share_link_opened"]) == 1


def test_the_sender_delivers_retries_and_gives_up_without_leaking_the_key(admin):
    f = firm(admin)
    sign(admin, f, f["revision"], "gate1", f["designer"])
    sent: list[dict] = []
    n = process_outbox(DB_URL, fake_mailer(sent))
    assert n >= 1 and sent and all(m["auth"] == "Bearer re_test_key" and "https://app.example.invalid/projects/" in m["text"] for m in sent)
    assert {s for (s,) in admin.execute("select status from notification where firm_id = %s", (f["firm"],)).fetchall()} == {"sent"}
    sign(admin, f, f["revision"], "gate2", f["checker"])
    bad: list[dict] = []
    for _ in range(3):
        process_outbox(DB_URL, fake_mailer(bad, status=500))
    states = {s for (s,) in admin.execute("select status from notification where firm_id = %s and status <> 'sent'", (f["firm"],)).fetchall()}
    assert states == {"failed"}
    errors = [e for (e,) in admin.execute("select error from notification where firm_id = %s and status = 'failed'", (f["firm"],)).fetchall()]
    assert errors and all("re_test_key" not in (e or "") for e in errors)
    assert uid()


def test_a_person_sees_only_their_own_outbox(admin):
    import psycopg
    f = firm(admin)
    sign(admin, f, f["revision"], "gate1", f["designer"])
    with psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": str(f["checker"])}),))
        rows = c.execute("select user_id from notification").fetchall()
    assert rows and {str(r[0]) for r in rows} == {str(f["checker"])}
