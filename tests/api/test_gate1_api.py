"""Gate 1 API: the repository and user are injected; an in-memory repo mimics the DB trigger semantics.

The trigger semantics: a client write stores provenance 'default' and clears confirmed_by (an edit withdraws
confirmation); only confirm() sets engineer_confirmed and confirmed_by. These tests prove the API cannot be used
to forge a confirmation.
"""
import itertools
import uuid
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep.api.app import create_app
from mep.api.schedule import CurrentUser, RevisionFrozenError
from mep.engine.loader import load_pack

ROOT = Path(__file__).resolve().parents[2]
REV, FIRM, OTHER_FIRM = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
DEAD25 = "NCC2025-J6D3-deadband"
HEALTH = {"score_percent": 40, "threshold_percent": 70, "below_threshold": True, "fixes": ["fix the model"]}
RUN_INPUTS = {"control_deadband": (3, "K"), "specialised_application_smaller_range_claimed": (False, None),
              "system_type": ("air_conditioning", None), "is_electricity_substation": (False, None)}


class RecordingLedger:
    def __init__(self):
        self.events = []

    def write(self, kind, payload, *, firm_id, revision_id):
        self.events.append((kind, payload, firm_id, revision_id))


class InMemoryRepo:
    def __init__(self, frozen=False):
        self.frozen = frozen
        self.parts, self.spaces, self.inputs, self.confirm_calls = [], {}, {}, []
        self.state, self.edition, self.cls, self.on = "VIC", "NCC2025", "5", date(2026, 10, 6)
        self.confirm_project = True
        self.confirm_parts = True          # the seeded part is confirmed unless a test says otherwise
        self.ids = (f"id{n}" for n in itertools.count(1))

    def _ok(self, rev, firm):
        return rev == REV and firm == FIRM

    def _public(self, row):
        return {k: v for k, v in row.items() if k not in ("confirmed_by", "system")}

    def get_gate1_view(self, rev, firm):
        if not self._ok(rev, firm):
            return None
        return {"project": {"id": "proj1", "state": self.state, "ncc_edition": self.edition, "climate_zone": 6,
                            "approval_date": str(self.on), "confirmed": self.confirm_project},
                "parts": [{k: v for k, v in x.items() if k != "confirmed_by"} for x in self.parts],
                "ncc_edition": "2025", "health": HEALTH,
                "spaces": [self._public(r) for r in self.spaces.values()],
                "inputs": [self._public(r) for r in self.inputs.values()]}

    def revision_frozen(self, rev, firm):
        return self.frozen

    def replace_parts(self, rev, firm, parts):
        self.parts = [{"id": next(self.ids), "confirmed_by": None, **p} for p in parts]  # an edit withdraws it
        return [{k: v for k, v in x.items() if k != "confirmed_by"} for x in self.parts]

    def _write(self, store, row_id, fields, base):
        if row_id is not None and row_id not in store:
            return None
        row = store.get(row_id) or {"id": next(self.ids), **base}
        row.update(fields)
        row.update(provenance="default", confirmed_by=None)  # trigger: client writes are default, edit withdraws
        store[row["id"]] = row
        return self._public(row)

    def upsert_space(self, rev, firm, space_id, data, manual_trace):
        return self._write(self.spaces, space_id, data, {"manual_trace": manual_trace})

    def upsert_input(self, rev, firm, input_id, data):
        return self._write(self.inputs, input_id, data, {"system": "ahu"})

    def confirm(self, groups, user_id, revision_id=None, etags=None):
        for kind, ids in groups.items():
            self.confirm_calls.append((kind, ids, user_id))
            for i in ids:
                if kind == "project":
                    self.confirm_project = True
                    continue
                if kind == "building_part":
                    next(x for x in self.parts if x["id"] == i)["confirmed_by"] = user_id
                    continue
                row = (self.spaces if kind == "space" else self.inputs)[i]
                row.update(provenance="engineer_confirmed", confirmed_by=user_id)

    def load_run_inputs(self, rev, firm):
        parts = self.parts or [{"id": "seed", "building_class": self.cls, "storeys": 3, "area_m2": 900.0,
                                "confirmed_by": uuid.uuid4() if self.confirm_parts else None}]
        return {"project": {"state": self.state, "ncc_edition": self.edition, "climate_zone": 6,
                            "building_class": parts, "approval_date": self.on,
                            "confirmed_by": uuid.uuid4() if self.confirm_project else None},
                "spaces": list(self.spaces.values()),
                "systems": [{"id": "ahu", "rules": [DEAD25], "part": None, "inputs": list(self.inputs.values())}]}


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "rules")


def make(pack, role="designer", firm=FIRM, repo=None):
    repo = repo or InMemoryRepo()
    user = CurrentUser(uuid.uuid4(), firm, role)
    repo.ledger = RecordingLedger()
    return TestClient(create_app(repo, lambda: user, pack, ledger=repo.ledger)), repo, user


def base(rev=REV):
    return f"/revisions/{rev}"


def load_inputs(c):
    ids = []
    for name, (value, unit) in RUN_INPUTS.items():
        r = c.post(f"{base()}/gate1/inputs", json={"name": name, "value": value, "unit": unit})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    return ids


def test_get_returns_the_contract_and_passes_health_through(pack):
    c, *_ = make(pack)
    body = c.get(f"{base()}/gate1").json()
    assert set(body) == {"parts", "spaces", "inputs", "health", "ncc_edition", "project", "role"}
    assert body["health"] == HEALTH and body["ncc_edition"] == "2025"


def test_other_firms_revision_is_404_everywhere(pack):
    c, *_ = make(pack, firm=OTHER_FIRM)
    for method, path, kw in [("get", "/gate1", {}), ("put", "/gate1/parts", {"json": {"parts": []}}),
                             ("post", "/gate1/spaces", {"json": {}}), ("post", "/run-rules", {}),
                             ("post", "/gate1/confirm", {"json": {"rows": [{"kind": "space", "id": "x"}]}})]:
        assert getattr(c, method)(base() + path, **kw).status_code in (404, 422), path
    assert c.get(f"{base()}/gate1").status_code == 404
    assert c.post(f"{base()}/run-rules").status_code == 404
    assert c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": "x"}]}).status_code == 404


def test_unknown_revision_is_404(pack):
    c, *_ = make(pack)
    assert c.get(base(uuid.uuid4()) + "/gate1").status_code == 404


GOOD = {"building_class": "5", "storeys": 3, "area_m2": 1200.5}


def test_parts_are_replaced_wholesale(pack):
    c, repo, _ = make(pack)
    c.put(f"{base()}/gate1/parts", json={"parts": [GOOD, {**GOOD, "building_class": "7a"}]})
    r = c.put(f"{base()}/gate1/parts", json={"parts": [GOOD]})
    assert r.status_code == 200 and len(r.json()) == 1 and len(repo.parts) == 1 and "id" in r.json()[0]


@pytest.mark.parametrize("part", [
    {**GOOD, "building_class": "10"}, {**GOOD, "building_class": "7"}, {**GOOD, "storeys": 0},
    {**GOOD, "storeys": 201}, {**GOOD, "storeys": 2.5}, {**GOOD, "area_m2": 0}, {**GOOD, "area_m2": -1},
    {**GOOD, "area_m2": 10_000_001}, {**GOOD, "area_m2": "big"}])
def test_bad_parts_are_422(pack, part):
    c, repo, _ = make(pack)
    assert c.put(f"{base()}/gate1/parts", json={"parts": [part]}).status_code == 422
    assert repo.parts == []


def test_part_count_is_1_to_50(pack):
    c, *_ = make(pack)
    assert c.put(f"{base()}/gate1/parts", json={"parts": []}).status_code == 422
    assert c.put(f"{base()}/gate1/parts", json={"parts": [GOOD] * 51}).status_code == 422
    assert c.put(f"{base()}/gate1/parts", json={"parts": [GOOD] * 50}).status_code == 200


@pytest.mark.parametrize("forged", ["engineer_confirmed", "extracted", "address_lookup_confirmed"])
def test_forged_provenance_on_a_space_is_ignored(pack, forged):
    c, repo, _ = make(pack)
    r = c.post(f"{base()}/gate1/spaces", json={"name": "Plant", "area_m2": 20, "provenance": forged,
                                               "confirmed_by": str(uuid.uuid4())})
    assert r.status_code == 200 and r.json()["provenance"] == "default"
    sid = r.json()["id"]
    assert repo.spaces[sid]["confirmed_by"] is None
    r = c.put(f"{base()}/gate1/spaces/{sid}", json={"name": "Plant 2", "provenance": forged})
    assert r.json()["provenance"] == "default" and repo.spaces[sid]["confirmed_by"] is None


@pytest.mark.parametrize("forged", ["engineer_confirmed", "extracted"])
def test_forged_provenance_on_an_input_is_ignored(pack, forged):
    c, repo, _ = make(pack)
    r = c.post(f"{base()}/gate1/inputs", json={"name": "control_deadband", "value": 3, "unit": "K",
                                               "provenance": forged, "confirmed_by": "x"})
    assert r.status_code == 200 and r.json()["provenance"] == "default"
    assert repo.inputs[r.json()["id"]]["confirmed_by"] is None


def test_a_numeric_input_needs_a_unit(pack):
    c, *_ = make(pack)
    assert c.post(f"{base()}/gate1/inputs", json={"name": "control_deadband", "value": 3}).status_code == 422
    assert c.post(f"{base()}/gate1/inputs", json={"name": "system_type", "value": "x", "unit": "K"}).status_code == 422
    assert c.post(f"{base()}/gate1/inputs", json={"name": "Bad Name", "value": 1, "unit": "K"}).status_code == 422


def test_editing_a_confirmed_row_withdraws_the_confirmation(pack):
    c, repo, _ = make(pack)
    sid = c.post(f"{base()}/gate1/spaces", json={"name": "Plant"}).json()["id"]
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": sid}]})
    assert repo.spaces[sid]["provenance"] == "engineer_confirmed"
    c.put(f"{base()}/gate1/spaces/{sid}", json={"name": "Plant B"})
    assert repo.spaces[sid]["provenance"] == "default" and repo.spaces[sid]["confirmed_by"] is None


def test_editing_an_unknown_row_is_404(pack):
    c, *_ = make(pack)
    assert c.put(f"{base()}/gate1/spaces/nope", json={"name": "x"}).status_code == 404
    assert c.put(f"{base()}/gate1/inputs/nope", json={"name": "x_y", "value": 1, "unit": "K"}).status_code == 404


@pytest.mark.parametrize("role", ["client", "reviewer", "", "admin"])
def test_only_a_designer_confirms(pack, role):
    c, repo, _ = make(pack, role=role)
    sid = "s1"
    repo.spaces[sid] = {"id": sid, "name": "Plant", "provenance": "default", "confirmed_by": None, "manual_trace": False}
    r = c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": sid}]})
    assert r.status_code == 403 and repo.confirm_calls == []


def test_the_role_cannot_be_supplied_in_the_body(pack):
    c, repo, _ = make(pack, role="client")
    sid = "s1"
    repo.spaces[sid] = {"id": sid, "name": "Plant", "provenance": "default", "confirmed_by": None, "manual_trace": False}
    r = c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": sid}], "role": "designer"})
    assert r.status_code in (403, 422) and repo.confirm_calls == []
    r = c.post(f"{base()}/gate1/confirm?role=designer", headers={"X-Role": "designer"},
               json={"rows": [{"kind": "space", "id": sid}]})
    assert r.status_code == 403 and repo.confirm_calls == []


def test_confirm_calls_the_repo_with_the_user_and_returns_the_rows(pack):
    c, repo, user = make(pack)
    sid = c.post(f"{base()}/gate1/spaces", json={"name": "Plant"}).json()["id"]
    iid = load_inputs(c)[0]
    rows = [{"kind": "space", "id": sid}, {"kind": "system_input", "id": iid}]
    r = c.post(f"{base()}/gate1/confirm", json={"rows": rows})
    assert r.status_code == 200 and r.json() == {"confirmed": rows}
    assert ("space", [sid], user.user_id) in repo.confirm_calls
    assert ("system_input", [iid], user.user_id) in repo.confirm_calls
    assert repo.spaces[sid]["confirmed_by"] == user.user_id


def test_confirm_refuses_rows_not_in_this_revision_and_empty_lists(pack):
    c, repo, _ = make(pack)
    assert c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": "other"}]}).status_code == 404
    assert c.post(f"{base()}/gate1/confirm", json={"rows": []}).status_code == 422
    assert c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "part", "id": "a"}]}).status_code == 422
    assert repo.confirm_calls == []


def test_a_frozen_revision_refuses_every_edit_with_409(pack):
    c, repo, _ = make(pack, repo=InMemoryRepo(frozen=True))
    for method, path, body in [("put", "/gate1/parts", {"parts": [GOOD]}), ("post", "/gate1/spaces", {"name": "a"}),
                               ("post", "/gate1/inputs", {"name": "a_b", "value": 1, "unit": "K"}),
                               ("post", "/gate1/confirm", {"rows": [{"kind": "space", "id": "x"}]})]:
        r = getattr(c, method)(base() + path, json=body)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "revision_frozen", path
    assert repo.parts == [] and repo.spaces == {} and repo.confirm_calls == []


def test_a_frozen_error_from_the_repo_is_409(pack):
    class Racy(InMemoryRepo):
        def upsert_space(self, *a):
            raise RevisionFrozenError

    c, *_ = make(pack, repo=Racy())
    assert c.post(f"{base()}/gate1/spaces", json={"name": "a"}).status_code == 409


def test_manual_trace_space_is_flagged_and_unconfirmed_until_confirmed(pack):
    c, repo, _ = make(pack)
    r = c.post(f"{base()}/gate1/spaces", json={"name": "Traced", "area_m2": 12, "manual_trace": True})
    row = r.json()
    assert row["manual_trace"] is True and row["ifc_guid"] is None and row["provenance"] == "default"
    assert c.post(f"{base()}/gate1/spaces", json={"manual_trace": True, "ifc_guid": "g"}).status_code == 422
    assert repo.spaces[row["id"]]["confirmed_by"] is None
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "space", "id": row["id"]}]})
    after = next(s for s in c.get(f"{base()}/gate1").json()["spaces"] if s["id"] == row["id"])
    assert after["provenance"] == "engineer_confirmed" and after["manual_trace"] is True


def test_run_is_refused_before_confirmation_with_gate1_required(pack):
    c, *_ = make(pack)
    load_inputs(c)
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "gate1_required" and r.json()["message"]


def test_an_unconfirmed_space_alone_blocks_the_run(pack):
    c, *_ = make(pack)
    ids = load_inputs(c)
    c.post(f"{base()}/gate1/spaces", json={"name": "Plant"})
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "system_input", "id": i} for i in ids]})
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "gate1_required"


@pytest.mark.parametrize("kind", ["space", "input"])
def test_extracted_rows_block_the_run_with_extracted_inputs(pack, kind):
    c, repo, _ = make(pack)
    ids = load_inputs(c)
    sid = c.post(f"{base()}/gate1/spaces", json={"name": "Plant"}).json()["id"]
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "system_input", "id": i} for i in ids]
                                            + [{"kind": "space", "id": sid}]})
    (repo.spaces[sid] if kind == "space" else repo.inputs[ids[0]])["provenance"] = "extracted"
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "extracted_inputs"


def test_engine_still_refuses_confirmed_provenance_without_a_recorded_confirmation(pack):
    """Defence in depth: a row written around the API (provenance set, confirmed_by missing) never runs."""
    c, repo, _ = make(pack)
    load_inputs(c)
    for row in repo.inputs.values():
        row["provenance"] = "engineer_confirmed"
    assert c.post(f"{base()}/run-rules").json()["code"] == "gate1_required"


def test_run_succeeds_after_confirmation_with_the_real_engine(pack):
    c, _repo, _ = make(pack)
    ids = load_inputs(c)
    sid = c.post(f"{base()}/gate1/spaces", json={"name": "Plant"}).json()["id"]
    r = c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "system_input", "id": i} for i in ids]
                                                + [{"kind": "space", "id": sid}]})
    assert r.status_code == 200
    run = c.post(f"{base()}/run-rules")
    assert run.status_code == 200, run.text
    body = run.json()
    assert body["run_id"] and body["report"]["results"][0]["outcome"] == "PASS"


def test_editing_after_confirmation_blocks_the_run_again(pack):
    c, _repo, _ = make(pack)
    ids = load_inputs(c)
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "system_input", "id": i} for i in ids]})
    assert c.post(f"{base()}/run-rules").status_code == 200
    c.put(f"{base()}/gate1/inputs/{ids[0]}", json={"name": "control_deadband", "value": 9, "unit": "K"})
    assert c.post(f"{base()}/run-rules").json()["code"] == "gate1_required"


def test_infinite_area_is_422(pack):
    c, repo, _ = make(pack)
    r = c.put(f"{base()}/gate1/parts", content='{"parts":[{"building_class":"5","storeys":1,"area_m2":1e999}]}',
              headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and repo.parts == []


def test_the_published_templates_are_served_and_nothing_else_is():
    from fastapi.testclient import TestClient
    from mep.api.app import create_app

    client = TestClient(create_app(object(), lambda: None))
    ok = client.get("/templates/mep-system-schedule-NCC2025.xlsx")
    assert ok.status_code == 200 and ok.content[:2] == b"PK"
    for name in ("..%2Fpyproject.toml", "mep-system-schedule-NCC2019.xlsx", "x.xlsx", "mep-system-schedule-NCC2025.xlsx.bak"):
        assert client.get(f"/templates/{name}").status_code == 404


# ---- review round: parts confirmation, ledger on the production path, designer-only edits --------------------

def _confirm_everything(c, repo):
    sid = c.post(f"{base()}/gate1/spaces", json={"name": "Plant"}).json()["id"]
    ids = [c.post(f"{base()}/gate1/inputs", json={"name": n, "value": v, "unit": u}).json()["id"]
           for n, (v, u) in RUN_INPUTS.items()]
    c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "system_input", "id": i} for i in ids]
                                            + [{"kind": "space", "id": sid}]})


def test_building_parts_without_a_recorded_confirmation_block_the_run(pack):
    c, repo, _ = make(pack)
    repo.confirm_parts = False
    _confirm_everything(c, repo)
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "gate1_required" and "building part 0" in r.json()["message"]


def test_parts_run_only_after_a_designer_confirms_them(pack):
    c, repo, _ = make(pack)
    _confirm_everything(c, repo)
    c.put(f"{base()}/gate1/parts", json={"parts": [GOOD]})          # a new list is unconfirmed again
    assert c.post(f"{base()}/run-rules").json()["code"] == "gate1_required"
    pid = c.get(f"{base()}/gate1").json()["parts"][0]["id"]
    assert c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "building_part", "id": pid}]}).status_code == 200
    assert c.post(f"{base()}/run-rules").status_code == 200


def test_a_legacy_text_class_without_parts_is_not_a_confirmed_class(pack):
    class Legacy(InMemoryRepo):
        def load_run_inputs(self, rev, firm):
            data = super().load_run_inputs(rev, firm)
            data["project"]["building_class"] = "5"
            return data

    c, repo, _ = make(pack, repo=Legacy())
    _confirm_everything(c, repo)
    assert c.post(f"{base()}/run-rules").json()["code"] == "gate1_required"


@pytest.mark.parametrize("role", ["checker", "approver", "client"])
def test_only_a_designer_edits_gate1_data(pack, role):
    c, repo, _ = make(pack, role=role)
    assert c.put(f"{base()}/gate1/parts", json={"parts": [GOOD]}).status_code == 403
    assert c.post(f"{base()}/gate1/spaces", json={"name": "x"}).status_code == 403
    assert c.post(f"{base()}/gate1/inputs", json={"name": "control_deadband", "value": 3, "unit": "K"}).status_code == 403
    assert repo.parts == [] and repo.spaces == {} and repo.inputs == {}


def test_a_double_refusal_through_the_run_endpoint_is_recorded_in_the_ledger(pack):
    c, repo, _ = make(pack)
    repo.state, repo.edition, repo.cls, repo.on = "NSW", "NCC2022", "2", date(2029, 1, 1)
    _confirm_everything(c, repo)
    r = c.post(f"{base()}/run-rules")
    # the 2025 rule is not selected for a 2022 project, so the run is also refused for that reason: the double
    # refusal must still be recorded
    assert r.status_code == 409 and r.json()["code"] == "rule_not_selected"
    assert [e[0] for e in repo.ledger.events] == ["run_refused"]
    _kind, payload, _firm_id, revision_id = repo.ledger.events[0]
    assert payload["refused_by"] == ["jurisdiction", "applicability"] and revision_id == str(REV)


def test_the_run_endpoint_refuses_to_run_without_a_configured_ledger(pack):
    repo = InMemoryRepo()
    user = CurrentUser(uuid.uuid4(), FIRM, "designer")
    c = TestClient(create_app(repo, lambda: user, pack))            # no ledger wired
    assert c.post(f"{base()}/run-rules").status_code == 503


def test_unconfirmed_project_facts_block_the_run_even_with_everything_else_confirmed(pack):
    """Review blocker: state, edition, climate zone and date had no recorded confirmation."""
    c, repo, _ = make(pack)
    repo.confirm_project = False
    _confirm_everything(c, repo)
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "gate1_required"
    assert "project facts" in r.json()["message"]


def test_a_designer_sees_and_confirms_the_project_facts_through_the_api(pack):
    c, repo, _ = make(pack)
    repo.confirm_project = False
    _confirm_everything(c, repo)
    assert c.post(f"{base()}/run-rules").json()["code"] == "gate1_required"
    project = c.get(f"{base()}/gate1").json()["project"]
    assert project["id"] == "proj1" and project["confirmed"] is False and project["approval_date"] == "2026-10-06"
    assert c.post(f"{base()}/gate1/confirm", json={"rows": [{"kind": "project", "id": project["id"]}]}).status_code == 200
    assert c.post(f"{base()}/run-rules").status_code == 200


def test_a_missing_approval_date_is_a_refusal_not_a_crash(pack):
    c, repo, _ = make(pack)
    repo.on = None
    _confirm_everything(c, repo)
    r = c.post(f"{base()}/run-rules")
    assert r.status_code == 409 and r.json()["code"] == "gate1_required" and "approval date" in r.json()["message"]
