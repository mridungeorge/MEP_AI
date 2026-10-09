"""Applicability (building-class carve-outs) is a separate check from the jurisdiction (edition) gate."""
from datetime import date
from pathlib import Path

import pytest
from mep.engine.adoption import load_adoption
from mep.engine.applicability import canonical_classes, check, load_applicability
from mep.engine.jurisdiction import Override, decide
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
APP = load_applicability()
DEAD22 = "NCC2022-J6D3-deadband"
D = date(2026, 10, 6)


def inputs22():
    p = Provenance.ENGINEER_CONFIRMED
    return {"control_deadband": InputValue(3, "K", p), "specialised_application_needs_smaller_deadband": InputValue(False, None, p),
            "system_type": InputValue("air_conditioning", None, p),
            "electricity_network_substation": InputValue(False, None, p)}


class Ledger:
    def __init__(self):
        self.events = []

    def write(self, kind, payload, *, firm_id, revision_id):
        self.events.append(payload)


def test_the_two_checks_are_independent_and_live_in_different_files():
    assert "carve_outs" in APP and "building_classes" in APP
    adoption = load_adoption()
    assert "building_classes" not in adoption
    text = (ROOT / "rules/adoption.yaml").read_text(encoding="utf-8")
    assert "refuse_classes" not in text and "refuse_reason" not in text
    # the edition gate knows nothing about classes: it allows NSW NCC 2022 for any class
    assert decide("NSW", "NCC2022", D, adoption).allowed


@pytest.mark.parametrize("state", ["NSW", "TAS"])
@pytest.mark.parametrize("cls", ["2", "4"])
def test_class_2_and_4_carve_outs_refuse_ncc2022(state, cls):
    d = check(state, "NCC2022", cls, APP)
    assert not d.allowed and f"class {cls}" in d.reasons[0]


@pytest.mark.parametrize("state", ["NSW", "TAS"])
def test_other_classes_and_the_other_edition_are_not_carved_out(state):
    for cls in ("3", "5", "6", "9b"):
        assert check(state, "NCC2022", cls, APP).allowed
    assert check(state, "NCC2025", "2", APP).allowed
    assert check("VIC", "NCC2022", "2", APP).allowed


def test_a_state_with_a_carve_out_needs_a_class():
    assert not check("TAS", "NCC2022", None, APP).allowed
    assert check("VIC", "NCC2022", None, APP).allowed


def test_state_whitespace_and_case_are_normalised():
    assert not check(" nsw ", "NCC2022", "2", APP).allowed


def test_every_carve_out_has_an_official_source_and_valid_classes():
    classes = canonical_classes(APP)
    for carve in APP["carve_outs"]:
        assert carve["source"].startswith("https://") and carve["source"].split("/")[2].endswith(".gov.au")
        assert set(carve["classes"]) <= classes and carve["effect"] == "refuse" and carve["reason"]


def project(state, cls, edition="NCC2022"):
    return ProjectFacts(state, edition, 6, cls, D, firm_id="f", revision_id="r")


def test_run_refuses_nsw_class_2_on_ncc2022_for_the_applicability_reason_only():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("NSW", "2"), [Subject("s", [DEAD22], inputs22())]), PACK)
    assert exc.value.code == "applicability"
    assert len(exc.value.reasons) == 1 and "BASIX" in exc.value.reasons[0]


def test_an_approver_override_covers_an_applicability_refusal_and_records_which_check_refused():
    ledger = Ledger()
    ov = Override("approver-1", "approver", "BASIX certificate version confirmed with the certifier")
    report = run(RunRequest(project("NSW", "2"), [Subject("s", [DEAD22], inputs22())], ov), PACK, ledger=ledger)
    assert report["jurisdiction"]["decision"] == "overridden"
    assert report["jurisdiction"]["refused_by"] == ["applicability"]
    assert ledger.events[0]["refused_by"] == ["applicability"]


def test_both_checks_can_refuse_at_once():
    ledger = Ledger()
    ov = Override("approver-1", "approver", "both confirmed with the client in writing")
    report = run(RunRequest(project("TAS", "2", "NCC2025"), [Subject("s", ["NCC2025-J6D3-deadband"], {})], ov),
                 PACK, ledger=ledger)           # NCC 2025 not yet in force in TAS on this date, class 2 is fine for 2025
    assert report["jurisdiction"]["refused_by"] == ["jurisdiction"]
    assert ledger.events[0]["refused_by"] == ["jurisdiction"]
    with pytest.raises(RunRefused):
        run(RunRequest(project("QLD", "2"), [Subject("s", [DEAD22], inputs22())]), PACK)


def test_an_edition_refusal_alone_does_not_mention_applicability():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project("NT", "5"), [Subject("s", [DEAD22], inputs22())]), PACK)
    assert all("does not apply to class" not in r for r in exc.value.reasons)
