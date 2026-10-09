"""The Postgres-backed API (mep.api.server) against the real local Supabase: JWT auth, RLS, provenance, the ledgered
Gate 1 confirmation, and the run that refuses while anything is unconfirmed. Needs `supabase db reset` (scripts/ci.sh).
"""
import os
import sys
import time
import uuid
from datetime import date
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls.conftest import DB_URL, uid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import e2e_lib

SECRET = os.environ.get("MEP_TEST_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long")
EDITION = "NCC2025"
AC = "air_conditioning"


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, SECRET, pack))


def seed(admin, *, approval=date(2026, 10, 1), state="VIC", edition=EDITION):
    """One firm with a designer, a checker, a project and an open revision. Returns ids."""
    f = {"firm": uid(), "project": uid(), "revision": uid(), "designer": uid(), "checker": uid()}
    admin.execute("insert into firm (id, name) values (%s, 'pg api test firm')", (f["firm"],))
    for role in ("designer", "checker"):
        admin.execute("insert into auth.users (id, email) values (%s, %s)", (f[role], f"{role}-{f[role]}@test.invalid"))
        admin.execute("insert into app_user (id, firm_id, role) values (%s, %s, %s)", (f[role], f["firm"], role))
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)"
                  " values (%s, %s, '1 Test St', %s, 6, %s, %s)", (f["project"], f["firm"], state, edition, approval))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                  (f["revision"], f["firm"], f["project"]))
    return f


def auth(user):
    return {"Authorization": f"Bearer {mint_token(SECRET, user)}"}


def base(f):
    return f"/revisions/{f['revision']}"


def populate(client, pack, f, tag="ahu-1"):
    """Parts, one space, and one fully filled form system, all written as the designer (all unconfirmed)."""
    h = auth(f["designer"])
    assert client.put(f"{base(f)}/gate1/parts", json={"parts": [
        {"building_class": "5", "storeys": 3, "area_m2": 1200.5}]}, headers=h).status_code == 200
    assert client.post(f"{base(f)}/gate1/spaces", json={"name": "Office", "area_m2": 80, "storey": "Level 1",
                                                       "use": "office"}, headers=h).status_code == 200
    r = client.post(f"{base(f)}/schedule/systems", json=e2e_lib.form_system(pack, EDITION, tag, AC), headers=h)
    assert r.status_code == 200, r.text


def rows_to_confirm(client, f):
    v = client.get(f"{base(f)}/gate1", headers=auth(f["designer"])).json()
    return ([{"kind": "project", "id": v["project"]["id"]}]
            + [{"kind": "building_part", "id": p["id"]} for p in v["parts"]]
            + [{"kind": "space", "id": s["id"]} for s in v["spaces"]]
            + [{"kind": "system_input", "id": i["id"]} for i in v["inputs"]])


def confirm(client, f, rows):
    return client.post(f"{base(f)}/gate1/confirm", json={"rows": rows}, headers=auth(f["designer"]))


# ---- authentication -------------------------------------------------------------------------------------------

def test_requests_without_a_valid_token_are_refused(admin, client):
    f = seed(admin)
    assert client.get(f"{base(f)}/gate1").status_code == 401
    assert client.get(f"{base(f)}/gate1", headers={"Authorization": "Basic abc"}).status_code == 401
    bad = jwt.encode({"sub": f["designer"], "aud": "authenticated", "exp": int(time.time()) + 600}, "x" * 40,
                     algorithm="HS256")
    assert client.get(f"{base(f)}/gate1", headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    expired = jwt.encode({"sub": f["designer"], "aud": "authenticated", "exp": int(time.time()) - 5}, SECRET,
                         algorithm="HS256")
    assert client.get(f"{base(f)}/gate1", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    no_exp = jwt.encode({"sub": f["designer"], "aud": "authenticated"}, SECRET, algorithm="HS256")
    assert client.get(f"{base(f)}/gate1", headers={"Authorization": f"Bearer {no_exp}"}).status_code == 401
    assert client.get(f"{base(f)}/gate1", headers=auth(uuid.uuid4())).status_code == 401      # not an app user


def test_a_role_claim_in_the_token_grants_nothing(admin, client):
    f = seed(admin)
    forged = jwt.encode({"sub": f["checker"], "aud": "authenticated", "exp": int(time.time()) + 600,
                         "role": "designer", "user_role": "designer", "firm_id": str(uuid.uuid4())}, SECRET,
                        algorithm="HS256")
    r = client.put(f"{base(f)}/gate1/parts", headers={"Authorization": f"Bearer {forged}"},
                   json={"parts": [{"building_class": "5", "storeys": 1, "area_m2": 10}]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    assert client.get(f"{base(f)}/gate1", headers={"Authorization": f"Bearer {forged}"}).status_code == 200


# ---- the view and RLS -----------------------------------------------------------------------------------------

def test_an_empty_revision_shows_the_project_facts_unconfirmed(admin, client):
    f = seed(admin)
    v = client.get(f"{base(f)}/gate1", headers=auth(f["designer"])).json()
    assert v["project"]["state"] == "VIC" and v["project"]["approval_date"] == "2026-10-01"
    assert v["project"]["confirmed"] is False and v["parts"] == [] and v["health"] is None
    assert v["ncc_edition"] == EDITION and v["role"] == "designer"


def test_another_firms_user_cannot_see_or_confirm_anything(admin, client, pack):
    a, b = seed(admin), seed(admin)
    populate(client, pack, a)
    rows = rows_to_confirm(client, a)
    assert client.get(f"{base(a)}/gate1", headers=auth(b["designer"])).status_code == 404
    r = client.post(f"{base(a)}/gate1/confirm", json={"rows": rows}, headers=auth(b["designer"]))
    assert r.status_code == 404
    assert client.put(f"{base(a)}/gate1/parts", headers=auth(b["designer"]),
                      json={"parts": [{"building_class": "5", "storeys": 1, "area_m2": 1}]}).status_code == 404
    # the ids of firm A's rows used against firm B's own revision are unknown there too
    assert client.post(f"{base(b)}/gate1/confirm", json={"rows": rows}, headers=auth(b["designer"])).status_code == 404
    assert admin.execute("select count(*) from ledger_event where kind = 'gate1_confirm' and firm_id = %s",
                         (a["firm"],)).fetchone()[0] == 0


# ---- provenance: never from the client -----------------------------------------------------------------------

def test_every_client_write_is_default_and_a_forged_provenance_is_not_stored(admin, client, pack):
    f = seed(admin)
    populate(client, pack, f)
    h = auth(f["designer"])
    forged = client.post(f"{base(f)}/gate1/spaces", headers=h, json={"name": "Hall", "area_m2": 5, "storey": "L1",
                                                                     "provenance": "engineer_confirmed",
                                                                     "confirmed_by": str(f["designer"])})
    assert forged.status_code == 200 and forged.json()["provenance"] == "default"
    sys_forged = e2e_lib.form_system(pack, EDITION, "ahu-2", AC)
    sys_forged["systems"][0]["inputs"][0]["provenance"] = "engineer_confirmed"
    assert client.post(f"{base(f)}/schedule/systems", json=sys_forged, headers=h).status_code == 422
    v = client.get(f"{base(f)}/gate1", headers=h).json()
    assert {i["provenance"] for i in v["inputs"]} == {"default"}
    assert admin.execute("select count(*) from space where revision_id = %s and confirmed_by is not null",
                         (f["revision"],)).fetchone()[0] == 0


def test_an_excel_import_is_extracted_until_a_designer_confirms_it(admin, client, pack):
    f = seed(admin)
    h = auth(f["designer"])
    data = e2e_lib.schedule_workbook(pack, EDITION, "ahu-x", AC)
    r = client.post(f"{base(f)}/schedule/import", headers=h, files={"file": ("s.xlsx", data, "application/octet-stream")})
    assert r.status_code == 200, r.text
    v = client.get(f"{base(f)}/gate1", headers=h).json()
    assert v["inputs"] and {i["provenance"] for i in v["inputs"]} == {"extracted"}
    assert {i["system"] for i in v["inputs"]} == {"ahu-x"}
    # a checker may not import
    r = client.post(f"{base(f)}/schedule/import", headers=auth(f["checker"]),
                    files={"file": ("s.xlsx", data, "application/octet-stream")})
    assert r.status_code == 403
    # confirming turns them into engineer_confirmed, with the confirming user recorded
    ids = [{"kind": "system_input", "id": i["id"]} for i in v["inputs"]]
    assert confirm(client, f, ids).status_code == 200
    after = client.get(f"{base(f)}/gate1", headers=h).json()
    assert {i["provenance"] for i in after["inputs"]} == {"engineer_confirmed"}
    # re-importing resets to extracted: the confirmation does not survive new data from a file
    client.post(f"{base(f)}/schedule/import", headers=h, files={"file": ("s.xlsx", data, "application/octet-stream")})
    again = client.get(f"{base(f)}/gate1", headers=h).json()
    assert {i["provenance"] for i in again["inputs"]} == {"extracted"}


# ---- confirmation ----------------------------------------------------------------------------------------------

def test_confirm_is_designer_only_and_every_confirmation_is_ledgered(admin, client, pack):
    f = seed(admin)
    populate(client, pack, f)
    rows = rows_to_confirm(client, f)
    r = client.post(f"{base(f)}/gate1/confirm", json={"rows": rows}, headers=auth(f["checker"]))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    assert admin.execute("select count(*) from ledger_event where kind = 'gate1_confirm' and firm_id = %s",
                         (f["firm"],)).fetchone()[0] == 0
    assert confirm(client, f, rows).status_code == 200
    events = admin.execute("select revision_id, payload from ledger_event where kind = 'gate1_confirm'"
                           " and firm_id = %s", (f["firm"],)).fetchall()
    assert {e[1]["kind"] for e in events} == {"project", "building_part", "space", "system_input"}
    assert all(str(e[0]) == f["revision"] and e[1]["confirmed_by"] == f["designer"] and e[1]["role"] == "designer"
               for e in events)
    v = client.get(f"{base(f)}/gate1", headers=auth(f["designer"])).json()
    assert v["project"]["confirmed"] and all(p["confirmed"] for p in v["parts"])
    assert {s["provenance"] for s in v["spaces"]} == {"engineer_confirmed"}


def test_an_edit_withdraws_the_confirmation(admin, client, pack):
    f = seed(admin)
    populate(client, pack, f)
    assert confirm(client, f, rows_to_confirm(client, f)).status_code == 200
    h = auth(f["designer"])
    v = client.get(f"{base(f)}/gate1", headers=h).json()
    space = v["spaces"][0]
    r = client.put(f"{base(f)}/gate1/spaces/{space['id']}", headers=h, json={"name": "Office 2"})
    assert r.status_code == 200 and r.json()["provenance"] == "default"
    assert r.json()["area_m2"] == space["area_m2"]            # a partial edit keeps the other fields
    # replacing the parts withdraws their confirmation too (new rows), and the run refuses again
    client.put(f"{base(f)}/gate1/parts", headers=h, json={"parts": [{"building_class": "5", "storeys": 3, "area_m2": 1200.5}]})
    after = client.get(f"{base(f)}/gate1", headers=h).json()
    assert not any(p["confirmed"] for p in after["parts"])


def test_project_facts_that_are_incomplete_cannot_be_confirmed(admin, client):
    f = seed(admin)
    admin.execute("update project set approval_date = null where id = %s", (f["project"],))
    v = client.get(f"{base(f)}/gate1", headers=auth(f["designer"])).json()
    r = confirm(client, f, [{"kind": "project", "id": v["project"]["id"]}])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "cannot_confirm"


def test_a_frozen_revision_refuses_edits_and_confirmation(admin, client, pack):
    f = seed(admin)
    populate(client, pack, f)
    rows = rows_to_confirm(client, f)
    admin.execute("update revision set frozen_at = now() where id = %s", (f["revision"],))
    h = auth(f["designer"])
    assert client.put(f"{base(f)}/gate1/parts", headers=h, json={"parts": [
        {"building_class": "5", "storeys": 1, "area_m2": 1}]}).status_code == 409
    assert client.post(f"{base(f)}/gate1/spaces", headers=h, json={"name": "x", "area_m2": 1, "storey": "1"}).status_code == 409
    assert confirm(client, f, rows).status_code == 409
    assert client.post(f"{base(f)}/schedule/systems", headers=h,
                       json=e2e_lib.form_system(pack, EDITION, "ahu-9", AC)).status_code == 409


# ---- the run ----------------------------------------------------------------------------------------------------

def test_a_fully_confirmed_revision_runs_and_returns_a_cited_draft_report(admin, client, pack):
    f = seed(admin)
    populate(client, pack, f)
    assert confirm(client, f, rows_to_confirm(client, f)).status_code == 200
    r = client.post(f"{base(f)}/run-rules", headers=auth(f["designer"]))
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    assert report["banner"] == "DRAFT RULES: NOT ENGINEER-APPROVED"
    assert report["results"] and all(x["citation"]["clause"] and x["citation"]["document"] for x in report["results"])
    assert {x["subject_id"] for x in report["results"]} == {"ahu-1"}


@pytest.mark.parametrize("leave", ["project", "building_part", "space", "system_input"])
def test_the_run_refuses_while_any_one_kind_is_unconfirmed(admin, client, pack, leave):
    f = seed(admin)
    populate(client, pack, f)
    rows = rows_to_confirm(client, f)
    left = [r for r in rows if r["kind"] == leave][:1]
    assert left, leave
    assert confirm(client, f, [r for r in rows if r not in left]).status_code == 200
    r = client.post(f"{base(f)}/run-rules", headers=auth(f["designer"]))
    assert r.status_code == 409 and r.json()["code"] == "gate1_required", r.text
    assert confirm(client, f, left).status_code == 200            # confirming the last one lets it run
    assert client.post(f"{base(f)}/run-rules", headers=auth(f["designer"])).status_code == 200


def test_the_run_refuses_extracted_inputs_even_when_everything_else_is_confirmed(admin, client, pack):
    f = seed(admin)
    h = auth(f["designer"])
    populate(client, pack, f)
    data = e2e_lib.schedule_workbook(pack, EDITION, "ahu-x", AC)
    client.post(f"{base(f)}/schedule/import", headers=h, files={"file": ("s.xlsx", data, "application/octet-stream")})
    v = client.get(f"{base(f)}/gate1", headers=h).json()
    rows = [r for r in rows_to_confirm(client, f) if not (r["kind"] == "system_input" and any(
        i["id"] == r["id"] and i["system"] == "ahu-x" for i in v["inputs"]))]
    assert confirm(client, f, rows).status_code == 200
    r = client.post(f"{base(f)}/run-rules", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "extracted_inputs"


def test_the_run_is_in_the_firms_scope_only(admin, client, pack):
    a, b = seed(admin), seed(admin)
    populate(client, pack, a)
    assert client.post(f"{base(a)}/run-rules", headers=auth(b["designer"])).status_code == 404
