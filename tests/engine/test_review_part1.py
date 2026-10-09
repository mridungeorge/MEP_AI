"""Regression tests for the adversarial review of Part 1 steps 2-4."""
import copy
from datetime import date
from pathlib import Path

import pytest
import yaml
from mep.engine.adoption import load_adoption
from mep.engine.applicability import check, load_applicability, validate
from mep.engine.jurisdiction import Override
from mep.engine.loader import RuleLoadError, load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
APP = load_applicability()
ADOPTION = load_adoption()
STATES = set(ADOPTION["jurisdictions"])
DEAD22 = "NCC2022-J6D3-deadband"
D = date(2026, 10, 6)


def inputs22():
    p = Provenance.ENGINEER_CONFIRMED
    return {"control_deadband": InputValue(3, "K", p),
            "specialised_application_needs_smaller_deadband": InputValue(False, None, p),
            "system_type": InputValue("air_conditioning", None, p),
            "electricity_network_substation": InputValue(False, None, p)}


class Ledger:
    def __init__(self):
        self.events = []

    def write(self, kind, payload, *, firm_id, revision_id):
        self.events.append((kind, payload))


OV = Override("approver-1", "approver", "BASIX certificate version confirmed with the certifier")


# ---- malformed applicability data fails closed -------------------------------------------------

def bad(**changes):
    data = copy.deepcopy(APP)
    data["carve_outs"][0].update(changes)
    return data


@pytest.mark.parametrize("changes", [
    {"state": "nsw"}, {"state": "Qld"}, {"state": "N.S.W."}, {"edition": "NCC 2022"}, {"edition": "ncc2022"},
    {"effect": "Refuse"}, {"effect": "allow"}, {"classes": []}, {"classes": None}, {"classes": ["99"]},
    {"reason": ""}, {"source": "http://example.com/x"}, {"source": "https://example.com/x"}, {"status": "guess"},
    {"id": ""},
])
def test_a_malformed_carve_out_is_a_load_error_not_a_silent_widening(changes):
    assert validate(bad(**changes), STATES)


def test_structural_garbage_is_reported_not_a_crash():
    assert validate(None, STATES) and validate({"carve_outs": None, "building_classes": ["5"]}, STATES)
    data = copy.deepcopy(APP)
    data["carve_outs"].insert(0, "oops")
    assert validate(data, STATES)
    data = copy.deepcopy(APP)
    data["carve_outs"].append(copy.deepcopy(data["carve_outs"][0]))     # duplicate id
    assert validate(data, STATES)
    assert validate({**APP, "building_classes": ["5", "5"]}, STATES)


def test_check_refuses_to_run_on_malformed_data():
    with pytest.raises(ValueError):
        check("NSW", "NCC2022", "2", {"carve_outs": None})
    with pytest.raises(ValueError):
        check("NSW", "NCC2022", "2", {"carve_outs": ["x"]})


def test_the_real_file_is_valid_and_a_broken_file_stops_the_run(tmp_path, monkeypatch):
    assert validate(APP, STATES) == []
    broken = tmp_path / "applicability.yaml"
    broken.write_text("building_classes: ['5']\ncarve_outs: null\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_applicability(broken)
    project = ProjectFacts("VIC", "NCC2025", 6, "5", D)
    req = RunRequest(project, [Subject("s", [], {})])
    with pytest.raises(RunRefused) as exc:
        run(req, PACK, applicability_data={"building_classes": ["5"], "carve_outs": None})
    assert exc.value.code == "invalid_data" or exc.value.code == "invalid_request"


# ---- state is exactly one of the adoption states; override kinds ------------------------------

@pytest.mark.parametrize("state", ["N.S.W.", "NSW" + chr(0x200B), "ＮＳＷ", "New South Wales", "XX", ""])
def test_unrecognised_state_spellings_are_refused_and_cannot_be_overridden(state):
    req = RunRequest(ProjectFacts(state, "NCC2022", 6, "2", D), [Subject("s", [DEAD22], inputs22())], OV)
    with pytest.raises(RunRefused) as exc:
        run(req, PACK, ledger=Ledger())
    assert exc.value.code == "invalid_request"


def project(state, cls, edition="NCC2022"):
    return ProjectFacts(state, edition, 6, cls, D, firm_id="f", revision_id="r")


def test_an_applicability_only_override_is_a_different_ledger_kind_and_refusal_code():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("NSW", "2"), [Subject("s", [DEAD22], inputs22())]), PACK)
    assert exc.value.code == "applicability"
    ledger = Ledger()
    run(RunRequest(project("NSW", "2"), [Subject("s", [DEAD22], inputs22())], OV), PACK, ledger=ledger)
    assert ledger.events[0][0] == "applicability_override" and ledger.events[0][1]["refused_by"] == ["applicability"]


def test_an_edition_override_keeps_the_jurisdiction_kind():
    ledger = Ledger()
    run(RunRequest(project("QLD", "5"), [Subject("s", [DEAD22], inputs22())], OV), PACK, ledger=ledger)
    assert ledger.events[0][0] == "jurisdiction_override"


def test_both_checks_refusing_is_recorded_as_jurisdiction_with_both_names():
    ledger = Ledger()
    # TAS NCC2022 class 2 on a date where NCC 2022 is still allowed: only applicability refuses
    run(RunRequest(project("TAS", "2"), [Subject("s", [DEAD22], inputs22())], OV), PACK, ledger=ledger)
    assert ledger.events[0][1]["refused_by"] == ["applicability"]


# ---- the loader enforces the sign-off rules itself --------------------------------------------

def _pack(tmp_path, **fields):
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True, exist_ok=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text(encoding="utf-8"))
    raw = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    raw.update(fields)
    (rules / "ncc2025" / "j6").mkdir(parents=True, exist_ok=True)
    (rules / "ncc2025" / "j6" / f"{raw['id']}.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return rules


@pytest.mark.parametrize("fields", [{"reviewed_by": "E. Engineer"}, {"reviewed_on": "2026-10-07"},
                                    {"reviewer_registration_no": "RPEQ 1"}, {"checked_by": "A. Checker"},
                                    {"checked_on": "2026-10-07"}])
def test_the_loader_refuses_sign_off_on_a_draft_and_half_a_check(tmp_path, fields):
    with pytest.raises(RuleLoadError):
        load_pack(_pack(tmp_path, **fields))


def test_the_loader_accepts_a_complete_first_pass_check_on_a_draft(tmp_path):
    assert load_pack(_pack(tmp_path, checked_by="A. Checker", checked_on="2026-10-07")).rules
