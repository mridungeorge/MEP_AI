"""Fix hypotheses end to end: options come from the engine, scratch changes are records, applying needs a designer, an open revision and a fresh engine verdict,
and the applied value is unconfirmed until Gate 1 again. Needs the local Supabase."""
import psycopg
import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

PACK = load_pack(lin.ROOT / "rules")
FAILING = ("ahu-1", "NCC2025-J6D3-time-switch-ac")


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, PACK, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def run_revision(admin, client):
    f = h.seed(admin)
    h.populate(client, PACK, f)
    assert h.confirm(client, f, h.rows_to_confirm(client, f)).status_code == 200
    assert client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"])).status_code == 200
    return f


def url(f, *parts):
    return f"/revisions/{f['revision']}/results/{FAILING[0]}/{FAILING[1]}/fixes" + "".join(f"/{p}" for p in parts)


def test_a_failed_result_gets_labelled_engine_options_and_a_pass_gets_none(admin, client):
    f = run_revision(admin, client)
    d = h.auth(f["designer"])
    body = client.get(url(f), headers=d).json()
    assert body["outcome"] == "FAIL" and body["label"] == "Hypothesis: verify" and body["options"]
    for o in body["options"]:
        assert o["label"].startswith("Hypothesis: verify.") and o["target_after"] == "PASS" and isinstance(o["accepted"], bool) and o["id"]
    assert body["rule_text_hypotheses"] and all(t.startswith("Hypothesis: verify.") for t in body["rule_text_hypotheses"])
    passing = client.get(f"/revisions/{f['revision']}/results/ahu-1/NCC2025-J6D3-deadband/fixes", headers=d).json()
    assert passing["outcome"] == "PASS" and passing["options"] == []
    assert client.get(f"/revisions/{f['revision']}/results/ahu-1/NCC2025-NOPE/fixes", headers=d).status_code == 404
    assert client.get(url(f), headers=h.auth(f["checker"])).status_code == 200            # a checker may read the hypotheses


def test_applying_a_fix_writes_an_unconfirmed_value_that_must_pass_gate_1_again(admin, client):
    f = run_revision(admin, client)
    d = h.auth(f["designer"])
    options = client.get(url(f), headers=d).json()["options"]
    chosen = next(o for o in options if o["accepted"])
    assert client.post(url(f, chosen["id"], "scratch"), headers=h.auth(f["checker"])).status_code == 403
    assert client.post(url(f, "0" * 16, "scratch"), headers=d).status_code == 404                  # an option the engine did not produce
    sc = client.post(url(f, chosen["id"], "scratch"), headers=d)
    assert sc.status_code == 200 and sc.json()["accepted"] is True
    sid = sc.json()["scratch_id"]
    assert admin.execute("select count(*) from ledger_event where revision_id = %s and kind = 'fix_proposed'", (f["revision"],)).fetchone()[0] == 1
    # nothing has changed in the revision yet
    before = client.get(f"/revisions/{f['revision']}/gate1", headers=d).json()["inputs"]
    old = next(i for i in before if i["name"] == chosen["input"])
    assert old["provenance"] == "engineer_confirmed"
    assert client.post(f"/revisions/{f['revision']}/fix-scratch/{sid}/apply", headers=h.auth(f["checker"])).status_code == 403
    done = client.post(f"/revisions/{f['revision']}/fix-scratch/{sid}/apply", headers=d)
    assert done.status_code == 200 and "Gate 1" in done.json()["needs"]
    after = next(i for i in client.get(f"/revisions/{f['revision']}/gate1", headers=d).json()["inputs"] if i["name"] == chosen["input"])
    assert after["value"] == chosen["to"] and after["provenance"] != "engineer_confirmed"           # hand-entered, unconfirmed
    again = client.post(f"/revisions/{f['revision']}/fix-scratch/{sid}/apply", headers=d)
    assert again.status_code == 409
    run = client.post(f"/revisions/{f['revision']}/run-rules", headers=d)
    assert run.status_code == 409 and run.json()["code"] == "gate1_required"                           # it goes through Gate 1 again
    only = [{"kind": "system_input", "id": after["id"], "etag": after["etag"]}]                   # the one changed value (the rest stays confirmed)
    assert h.confirm(client, f, only).status_code == 200
    assert client.post(f"/revisions/{f['revision']}/run-rules", headers=d).status_code == 200
    now = client.get(f"/revisions/{f['revision']}/results", headers=d).json()["results"]
    assert next(r for r in now if (r["subject_id"], r["rule_id"]) == FAILING)["outcome"] == "PASS"
    kinds = {k for (k,) in admin.execute("select kind from ledger_event where revision_id = %s", (f["revision"],)).fetchall()}
    assert {"fix_proposed", "fix_applied"} <= kinds


def test_a_frozen_revision_takes_no_fix_and_a_client_cannot_write_scratch(admin, client):
    f = run_revision(admin, client)
    d = h.auth(f["designer"])
    chosen = next(o for o in client.get(url(f), headers=d).json()["options"] if o["accepted"])
    sid = client.post(url(f, chosen["id"], "scratch"), headers=d).json()["scratch_id"]
    admin.execute("update revision set frozen_at = now(), status = 'frozen' where id = %s", (f["revision"],))
    assert client.post(f"/revisions/{f['revision']}/fix-scratch/{sid}/apply", headers=d).status_code == 409
    assert client.post(url(f, chosen["id"], "scratch"), headers=d).status_code == 409
    with psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("insert into fix_scratch (firm_id, revision_id, subject_id, rule_id, option_id, option, cross_rule, accepted, created_by)"
                      " values (%s, %s, 'a', 'b', 'c', '{}', '{}', true, %s)", (f["firm"], f["revision"], f["designer"]))
    with pytest.raises(psycopg.errors.Error):                                                         # a record: it cannot be edited or deleted
        admin.execute("update fix_scratch set accepted = false where id = %s", (sid,))
    with pytest.raises(psycopg.errors.Error):
        admin.execute("delete from fix_scratch where id = %s", (sid,))
