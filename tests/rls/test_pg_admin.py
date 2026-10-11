"""Firm administration: invitations, roles, deactivation, settings, templates. Needs the local Supabase."""
import io
import json

import ezdxf
import psycopg
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL, uid

PACK = load_pack(lin.ROOT / "rules")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, PACK, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def firm_with_admin(admin):
    f = h.seed(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    return f


def hdr(uid_):
    return {"Authorization": f"Bearer {mint_token(h.SECRET, uid_)}"}


def new_login(admin, email, confirmed=True):
    u = uid()
    admin.execute("insert into auth.users (id, email, email_confirmed_at) values (%s, %s, %s)", (u, email, "2026-01-01" if confirmed else None))
    return u


def test_only_an_admin_administers_and_an_invitation_is_one_per_address(admin, client):
    f = firm_with_admin(admin)
    mail = f"new-{uid()}@test.invalid"
    assert client.post("/admin/invitations", headers=hdr(f["checker"]), json={"email": mail, "role": "checker"}).status_code == 403
    assert client.get("/admin/overview", headers=hdr(f["checker"])).status_code == 403
    ok = client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": mail.upper(), "role": "checker"})
    assert ok.status_code == 200
    assert client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": mail, "role": "designer"}).status_code == 409
    assert client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": "not-an-address", "role": "checker"}).status_code == 422
    assert client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": mail, "role": "root"}).status_code == 422
    ov = client.get("/admin/overview", headers=hdr(f["designer"])).json()
    assert [i["email"] for i in ov["invitations"]] == [mail] and {u["is_admin"] for u in ov["users"]} == {True, False}
    n = admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'user_invited' and payload::text not like %s",
                      (f["firm"], f"%{mail}%")).fetchone()[0]
    assert n == 1                                                      # the ledger records the invitation, never the address
    assert client.delete(f"/admin/invitations/{ok.json()['invitation_id']}", headers=hdr(f["designer"])).status_code == 200


def test_an_invited_person_joins_with_the_invited_role_only_with_a_confirmed_matching_address(admin, client):
    f = firm_with_admin(admin)
    mail = f"joiner-{uid()}@test.invalid"
    client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": mail, "role": "approver"})
    stranger = new_login(admin, f"stranger-{uid()}@test.invalid")
    assert client.get("/invitations/mine", headers=hdr(stranger)).json()["invitation"] is None
    assert client.post("/invitations/accept", headers=hdr(stranger)).status_code == 422
    spoof = new_login(admin, mail, confirmed=False)                                   # right address, not confirmed
    assert client.post("/invitations/accept", headers=hdr(spoof)).status_code in (403, 422)
    assert admin.execute("select count(*) from app_user where id = %s", (spoof,)).fetchone()[0] == 0
    admin.execute("delete from auth.users where id = %s", (spoof,))
    real = uid()
    admin.execute("insert into auth.users (id, email, email_confirmed_at) values (%s, %s, now())", (real, mail))
    mine = client.get("/invitations/mine", headers=hdr(real)).json()
    assert mine["invitation"]["role"] == "approver" and mine["member"] is False
    assert client.post("/invitations/accept", headers=hdr(real)).status_code == 200
    assert admin.execute("select role::text, firm_id::text, email, is_admin, active from app_user where id = %s", (real,)).fetchone() == \
        ("approver", str(f["firm"]), mail, False, True)
    assert client.get("/me", headers=hdr(real)).json()["role"] == "approver"
    assert client.post("/invitations/accept", headers=hdr(real)).status_code == 422          # already a member
    assert client.get("/admin/overview", headers=hdr(real)).status_code == 403              # a new approver is not an admin


def test_roles_admins_and_deactivation(admin, client):
    f = firm_with_admin(admin)
    d, c = hdr(f["designer"]), f["checker"]
    assert client.put(f"/admin/users/{c}", headers=d, json={"role": "approver"}).status_code == 200
    assert admin.execute("select role::text from app_user where id = %s", (c,)).fetchone()[0] == "approver"
    assert client.put(f"/admin/users/{f['designer']}", headers=d, json={"active": False}).status_code == 422           # not yourself
    assert client.put(f"/admin/users/{f['designer']}", headers=d, json={"is_admin": False}).status_code == 422          # the last admin
    assert client.put(f"/admin/users/{c}", headers=d, json={"is_admin": True}).status_code == 200
    assert client.put(f"/admin/users/{f['designer']}", headers=d, json={"is_admin": False}).status_code == 200
    assert client.get("/me", headers=hdr(f["designer"])).status_code == 200
    assert client.put(f"/admin/users/{f['designer']}", headers=hdr(c), json={"active": False}).status_code == 200
    gone = client.get("/me", headers=hdr(f["designer"]))
    assert gone.status_code == 403 and gone.json()["detail"]["code"] == "deactivated"
    n = admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'app_user_changed' and (payload ->> 'active') = 'false'",
                      (f["firm"],)).fetchone()[0]
    assert n == 1
    other = firm_with_admin(admin)                                                              # another firm's person is not reachable
    assert client.put(f"/admin/users/{other['designer']}", headers=hdr(c), json={"role": "checker"}).status_code == 422


def test_firm_settings_are_validated_and_ledgered(admin, client):
    f = firm_with_admin(admin)
    d = hdr(f["designer"])
    body = {"name": "Renamed Pty Ltd", "signer_mode": "small_firm", "sample_size": 25, "near_miss_default": 0.05}
    assert client.put("/admin/firm", headers=d, json=body).status_code == 200
    ov = client.get("/admin/overview", headers=d).json()["firm"]
    assert (ov["name"], ov["signer_mode"], ov["sample_size"], ov["near_miss_default"]) == ("Renamed Pty Ltd", "small_firm", 25, 0.05)
    for bad in ({**body, "sample_size": 0}, {**body, "near_miss_default": 0.9}, {**body, "signer_mode": "none"}, {**body, "name": ""}):
        assert client.put("/admin/firm", headers=d, json=bad).status_code == 422
    kinds = {r[0] for r in admin.execute("select kind from ledger_event where firm_id = %s", (f["firm"],)).fetchall()}
    assert {"firm_settings_changed", "firm_signer_mode_changed"} <= kinds
    assert client.put("/admin/firm", headers=hdr(f["checker"]), json=body).status_code == 403


def dxf_bytes(with_insert: bool = False) -> bytes:
    doc = ezdxf.new("R2010")
    doc.modelspace().add_line((0, 0), (420, 0))
    if with_insert:
        doc.blocks.new("TITLEBLOCK").add_line((0, 0), (420, 0))
        doc.modelspace().add_blockref("TITLEBLOCK", (0, 0))
    out = io.StringIO()
    doc.write(out)
    return out.getvalue().encode()


def test_title_block_and_layer_standard_uploads(admin, client):
    f = firm_with_admin(admin)
    d = hdr(f["designer"])
    layers = json.dumps({"layers": {"A-DUCT": {"color": 3, "linetype": "CONTINUOUS"}, "A-DUCT-HIDDEN": {"color": 8, "linetype": "HIDDEN"}}}).encode()

    def up(kind, data, who=d):
        return client.post("/admin/templates", headers=who, data={"kind": kind, "name": "Firm standard"}, files={"file": ("t", data)})

    assert up("layer_standard", layers).status_code == 200
    assert up("title_block", dxf_bytes()).status_code == 200
    for bad in (b"not json", json.dumps({"layers": {"bad name!": {"color": 3}}}).encode(), json.dumps({"layers": {"A": {"color": 999}}}).encode(),
                json.dumps({"layers": {}}).encode()):
        assert up("layer_standard", bad).status_code == 422
    assert up("title_block", b"garbage").status_code == 422
    assert up("title_block", dxf_bytes(with_insert=True)).status_code == 422                      # inserted blocks could expand without limit
    assert up("layer_standard", layers, hdr(f["checker"])).status_code == 403
    assert up("layer_standard", b"x" * (2 * 1024 * 1024 + 1)).status_code in (413, 422)
    ov = client.get("/admin/overview", headers=d).json()["templates"]
    assert sorted(t["kind"] for t in ov) == ["layer_standard", "title_block"]
    tid = next(t["id"] for t in ov if t["kind"] == "layer_standard")
    assert client.get(f"/admin/templates/{tid}/download", headers=d).content == layers
    assert client.get(f"/admin/templates/{tid}/download", headers=hdr(f["checker"])).status_code == 403
    other = firm_with_admin(admin)
    assert client.get(f"/admin/templates/{tid}/download", headers=hdr(other["designer"])).status_code == 404
    with pytest.raises(psycopg.errors.InsufficientPrivilege), psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select content from firm_template")


def test_nobody_administers_their_own_role_and_the_oracle_about_other_firms_is_closed(admin, client):
    f = firm_with_admin(admin)
    d = hdr(f["designer"])
    assert client.put(f"/admin/users/{f['designer']}", headers=d, json={"role": "approver"}).status_code == 422
    other = firm_with_admin(admin)
    theirs = admin.execute("select email from app_user where id = %s", (other["checker"],)).fetchone()[0]
    mine = admin.execute("select email from app_user where id = %s", (f["checker"],)).fetchone()[0]
    a = client.post("/admin/invitations", headers=d, json={"email": theirs, "role": "checker"})
    b = client.post("/admin/invitations", headers=d, json={"email": mine, "role": "checker"})
    assert a.status_code == b.status_code == 422 and a.json() == b.json()                      # the same answer for "another firm" and "your own"


def test_several_invitations_for_one_address_need_a_choice(admin, client):
    a, b = firm_with_admin(admin), firm_with_admin(admin)
    mail = f"two-{uid()}@test.invalid"
    for f, role in ((a, "checker"), (b, "approver")):
        assert client.post("/admin/invitations", headers=hdr(f["designer"]), json={"email": mail, "role": role}).status_code == 200
    me = uid()
    admin.execute("insert into auth.users (id, email, email_confirmed_at) values (%s, %s, now())", (me, mail))
    mine = client.get("/invitations/mine", headers=hdr(me)).json()
    assert len(mine["invitations"]) == 2 and mine["invitation"] is None
    assert client.post("/invitations/accept", headers=hdr(me)).status_code == 422               # no guessing
    pick = next(i["id"] for i in mine["invitations"] if i["role"] == "approver")
    assert client.post("/invitations/accept", headers=hdr(me), json={"invitation_id": pick}).status_code == 200
    assert admin.execute("select firm_id::text, role::text from app_user where id = %s", (me,)).fetchone() == (str(b["firm"]), "approver")


def test_two_administrators_cannot_leave_the_firm_without_one(admin, client):
    import threading
    f = firm_with_admin(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["checker"],))
    results: list[int] = []

    def drop(me, other):
        results.append(client.put(f"/admin/users/{me}", headers=hdr(other), json={"is_admin": False}).status_code)

    ts = [threading.Thread(target=drop, args=(f["designer"], f["checker"])), threading.Thread(target=drop, args=(f["checker"], f["designer"]))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert admin.execute("select count(*) from app_user where firm_id = %s and is_admin and active", (f["firm"],)).fetchone()[0] >= 1
