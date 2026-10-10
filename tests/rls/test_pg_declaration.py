"""The NSW design compliance declaration DRAFT: applicability, what it carries, what it refuses to say, and the validated PDF. Needs the local Supabase."""
import copy
import io

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from mep.review import declaration as decl
from pypdf import PdfReader

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls import test_pg_review_api as rv
from tests.rls.conftest import DB_URL

BANNER = "DRAFT: the registered design practitioner must review and lodge on the NSW Planning Portal"


def package(**over):
    p = {"banner": "DRAFT RULES: NOT ENGINEER-APPROVED", "independence_notice": None, "revision": {"id": "rev-1", "architect_rev": "B", "frozen_at": "2026-10-10", "derived_from": []},
         "project": {"address": "1 Test St", "state": "NSW", "climate_zone": 5, "ncc_edition": "NCC2022", "approval_date": "2026-01-01",
                     "building_parts": [{"class": "2", "storeys": 4, "area_m2": 1200.0}]},
         "summary": {"PASS": 3, "FAIL": 1}, "accepted_fails": [{"subject": "ahu-1", "rule_id": "R-1", "accepted_fail": {"category": "out_of_scope", "reference": "ref", "explanation": "x"}}],
         "results": [], "signoffs": [{"gate": g, "role": r, "email": f"{r}@x.test", "signed_at": "2026-10-10", "registration_no": reg} for g, r, reg in
                                     (("gate1", "designer", None), ("gate2", "checker", None), ("gate3", "approver", "RPEQ 12345"))],
         "status": {"signed_gates": ["gate1", "gate2", "gate3"], "complete": True, "missing": []},
         "ledger": {"verified": True, "events": 9, "anchor_seq": 9, "anchor_hash": "ab" * 32}}
    p.update(over)
    return p


def test_applicability_is_nsw_class_2_3_9c_signed_and_verified():
    assert decl.build(package(), [])["applies_to"] == {"state": "NSW", "classes": ["2"]}
    assert decl.build(package(project={**package()["project"], "building_parts": [{"class": "9C", "storeys": 1, "area_m2": 5}, {"class": "5", "storeys": 1, "area_m2": 5}]}), [])["applies_to"]["classes"] == ["9c"]
    for bad, code in ((package(project={**package()["project"], "state": "VIC"}), "not_nsw"),
                      (package(project={**package()["project"], "building_parts": [{"class": "5", "storeys": 1, "area_m2": 5}]}), "class_not_covered"),
                      (package(status={"signed_gates": ["gate1"], "complete": False, "missing": ["gate2", "gate3"]}), "not_signed"),
                      (package(ledger={"verified": False, "events": 1, "anchor_seq": None, "anchor_hash": None}), "ledger_unverified")):
        with pytest.raises(decl.NotApplicable) as e:
            decl.build(bad, [])
        assert e.value.code == code


def test_the_draft_states_nothing_and_every_page_says_so():
    d = decl.build(package(), [{"subject": "ahu-1", "rule_id": "R-2", "pathway": "PERFORMANCE_SOLUTION", "note": "model run", "evidence": [{"title": "Thermal model"}]},
                                {"subject": "ahu-1", "rule_id": "R-3", "pathway": "DTS", "note": None, "evidence": []}])
    assert d["banner"] == BANNER and d["lodged"] is False
    assert [x["rule_id"] for x in d["performance_solutions"]] == ["R-2"] and d["accepted_fails"][0]["rule_id"] == "R-1"
    assert {x["field"] for x in d["fields_to_complete"]} >= {"declaration_statement", "practitioner_identity", "planning_portal_reference"}
    data = decl.to_pdf(d)
    verdict = decl.validate_pdf(data, d)
    assert verdict["passed"], verdict
    text = " ".join("\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(data)).pages).split())
    assert BANNER in text and "has not been lodged" in text and "RPEQ 12345" in text and "Thermal model" in text
    assert decl.to_pdf(d) == data                                                       # deterministic
    # the validator refuses a PDF that lost its banner or listed a different revision
    other = copy.deepcopy(d)
    other["scope"]["revision_id"] = "another-revision"
    assert not decl.validate_pdf(data, other)["passed"]


@pytest.fixture(scope="module")
def pack():
    return load_pack(lin.ROOT / "rules")


@pytest.fixture(scope="module")
def client(pack):
    return TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))


def test_the_endpoint_follows_the_signed_package(admin, client, pack):
    f = rv.frozen(admin, client, pack)
    rev, de = f["revision"], rv.auth(f, "designer")
    admin.execute("set session_replication_role = replica")
    admin.execute("update project set state = 'NSW' where id = %s", (f["project"],))
    admin.execute("update building_part set building_class = '2' where project_id = %s", (f["project"],))
    admin.execute("set session_replication_role = origin")
    early = client.get(f"/revisions/{rev}/nsw-declaration", headers=de)
    assert early.status_code == 409 and early.json()["detail"]["code"] == "not_signed"                    # gates 2 and 3 are not signed yet
    rv.review_all(client, f)
    assert client.post(f"/revisions/{rev}/sign/gate2", headers=rv.auth(f, "checker"), json={}).status_code == 200
    rv.acknowledge_all(client, f)
    assert client.post(f"/revisions/{rev}/sign/gate3", headers=rv.auth(f, "approver"), json={"registration": "RPEQ 12345"}).status_code == 200
    got = client.get(f"/revisions/{rev}/nsw-declaration", headers=de)
    assert got.status_code == 200
    body = got.json()
    assert body["banner"] == BANNER and body["lodged"] is False and body["applies_to"]["classes"] == ["2"]
    assert [s["registration_no"] for s in body["signed_by"]][-1] == "RPEQ 12345"
    pdf = client.get(f"/revisions/{rev}/nsw-declaration.pdf", headers=de)
    assert pdf.status_code == 200 and pdf.headers["x-validator"] == "passed" and pdf.content[:5] == b"%PDF-"
    assert client.get(f"/revisions/{rev}/nsw-declaration", headers=h.auth(h.seed(admin)["designer"])).status_code in (404, 403)       # another firm's revision
    admin.execute("set session_replication_role = replica")
    admin.execute("update building_part set building_class = '5' where project_id = %s", (f["project"],))
    admin.execute("set session_replication_role = origin")
    assert client.get(f"/revisions/{rev}/nsw-declaration", headers=de).json()["detail"]["code"] == "class_not_covered"


def test_standards_slots_through_the_api_say_licence_required(admin, client):
    f = h.seed(admin)
    d = h.auth(f["designer"])
    got = client.get("/standards", headers=d).json()["slots"]
    assert {s["standard"] for s in got} == {"AS 1668.2", "AS/NZS 3000", "AS/NZS 3008", "AS 4254"} and all(s["state"] == "LICENCE_REQUIRED" and s["rules_loaded"] == 0 for s in got)
    per = client.get(f"/revisions/{f['revision']}/standards", headers=d).json()["slots"]
    assert all(s["results"] == [] and s["evaluated"] is False for s in per)
    assert client.get(f"/revisions/{h.seed(admin)['revision']}/standards", headers=d).status_code == 404
