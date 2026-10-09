"""Gate 1: the engine refuses to run on extracted or unconfirmed values, and runs once a designer has confirmed them."""
from datetime import date
from pathlib import Path

import pytest
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
DEAD25 = "NCC2025-J6D3-deadband"
P = ProjectFacts("VIC", "NCC2025", 6, "5", date(2026, 10, 6), firm_id="f", revision_id="r")


def inputs(**kw):
    c = Provenance.ENGINEER_CONFIRMED
    base = {"control_deadband": InputValue(3, "K", c),
            "specialised_application_smaller_range_claimed": InputValue(False, None, c),
            "system_type": InputValue("air_conditioning", None, c),
            "is_electricity_substation": InputValue(False, None, c)}
    base.update(kw)
    return base


def attempt(**kw):
    return run(RunRequest(P, [Subject("ahu", [DEAD25], inputs(**kw))]), PACK)


def test_a_run_on_confirmed_values_succeeds():
    assert attempt()["results"][0]["outcome"] == "PASS"


@pytest.mark.parametrize("prov", [Provenance.EXTRACTED, "extracted"])
def test_an_extracted_value_blocks_the_run_with_its_own_code(prov):
    with pytest.raises(RunRefused) as exc:
        attempt(control_deadband=InputValue(3, "K", prov))
    assert exc.value.code == "extracted_inputs" and "ahu.control_deadband" in exc.value.reasons[0]


def test_a_default_value_is_never_a_pass():
    """Defaults are unconfirmed: the rule engine answers NEEDS_JUDGEMENT, never PASS/FAIL, on a default value."""
    result = attempt(control_deadband=InputValue(3, "K", Provenance.DEFAULT))["results"][0]
    assert result["outcome"] == "NEEDS_JUDGEMENT"


def test_a_value_with_a_confirmed_kind_but_no_recorded_confirmation_blocks_the_run():
    """Engineer-confirmed provenance without a confirmed_by on the row (written around the API) is not a confirmation."""
    with pytest.raises(RunRefused) as exc:
        attempt(control_deadband=InputValue(3, "K", Provenance.ENGINEER_CONFIRMED, confirmed=False))
    assert exc.value.code == "gate1_required" and "ahu.control_deadband" in exc.value.reasons[0]


def test_every_unconfirmed_item_is_listed_not_just_the_first():
    with pytest.raises(RunRefused) as exc:
        attempt(control_deadband=InputValue(3, "K", Provenance.ENGINEER_CONFIRMED, confirmed=False),
                system_type=InputValue("air_conditioning", None, Provenance.ENGINEER_CONFIRMED, confirmed=False))
    assert len(exc.value.reasons) == 2


@pytest.mark.parametrize("flag", [1, "yes", None, 0.0])
def test_the_confirmed_flag_must_be_a_real_boolean(flag):
    with pytest.raises(RunRefused) as exc:
        attempt(control_deadband=InputValue(3, "K", Provenance.ENGINEER_CONFIRMED, confirmed=flag))
    assert exc.value.code == "invalid_request"


def test_extracted_wins_over_unconfirmed_when_both_are_present():
    with pytest.raises(RunRefused) as exc:
        attempt(control_deadband=InputValue(3, "K", Provenance.EXTRACTED),
                system_type=InputValue("air_conditioning", None, Provenance.ENGINEER_CONFIRMED, confirmed=False))
    assert exc.value.code == "extracted_inputs"


def test_after_confirmation_the_same_values_run():
    with pytest.raises(RunRefused):
        attempt(control_deadband=InputValue(3, "K", Provenance.EXTRACTED))
    assert attempt(control_deadband=InputValue(3, "K", Provenance.ENGINEER_CONFIRMED, confirmed=True))["results"]
