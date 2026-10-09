from datetime import date
from pathlib import Path

import pytest
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, Outcome, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
DEAD25 = "NCC2025-J6D3-deadband"
D = date(2026, 10, 6)


def iv(value, unit=None, prov=Provenance.ENGINEER_CONFIRMED):
    return InputValue(value, unit, prov)


def vic25(**kw):
    return ProjectFacts(**{"state": "VIC", "ncc_edition": "NCC2025", "climate_zone": 6, "building_class": "5",
                           "approval_date": D, **kw})


def econ_inputs(airflow, cycle=False, **over):
    base = {"max_airside_component_airflow": iv(airflow, "L/s"), "economy_cycle": iv(cycle),
            "system_type": iv("air_conditioning"), "provides_required_mech_ventilation": iv(True),
            "dehumidification_control_needed": iv(False), "is_electricity_substation": iv(False)}
    base.update(over)
    return base


def one(report, rule_id=None):
    results = [r for r in report["results"] if rule_id is None or r["rule_id"] == rule_id]
    assert len(results) == 1
    return results[0]


def run_econ(airflow, cycle=False, project=None, **over):
    req = RunRequest(project or vic25(), [Subject("ahu-1", [ECON25], econ_inputs(airflow, cycle, **over))])
    return run(req, PACK)


def test_pass_fail_boundaries_use_the_table_threshold_for_the_project_climate_zone():
    assert one(run_econ(1199.9))["outcome"] == "PASS"
    assert one(run_econ(1200))["outcome"] == "FAIL"        # trigger is 'at or above'
    assert one(run_econ(1200, cycle=True))["outcome"] == "PASS"


def test_climate_zone_one_is_not_applicable_and_other_zones_use_their_own_value():
    assert one(run_econ(5000, project=vic25(climate_zone=1)))["outcome"] == "NOT_APPLICABLE"
    assert one(run_econ(5000, project=vic25(climate_zone=2)))["outcome"] == "PASS"   # table value is higher


def test_near_miss_is_computed_by_perturbing_inputs_by_the_rules_fraction():
    near_pass = one(run_econ(1180))
    assert near_pass["outcome"] == "PASS" and near_pass["near_miss"]["is_near_miss"]
    assert near_pass["near_miss"]["fraction"] == 0.05
    assert near_pass["near_miss"]["flips"][0]["input"] == "max_airside_component_airflow"
    near_fail = one(run_econ(1250))
    assert near_fail["outcome"] == "FAIL" and near_fail["near_miss"]["is_near_miss"]
    clear = one(run_econ(1000))
    assert clear["outcome"] == "PASS" and not clear["near_miss"]["is_near_miss"]
    assert one(run_econ(3000))["near_miss"]["is_near_miss"] is False


def test_fix_hypotheses_only_on_fail_and_always_labelled():
    fail = one(run_econ(3000))
    assert fail["fix_hypotheses"] and all(h.startswith("Hypothesis: verify") for h in fail["fix_hypotheses"])
    assert one(run_econ(100))["fix_hypotheses"] == []


def test_every_result_has_a_citation_whatever_the_outcome():
    cases = [run_econ(1000), run_econ(3000), run_econ(5000, project=vic25(climate_zone=1)),
             run(RunRequest(vic25(), [Subject("s", [ECON25], {})]), PACK)]
    outcomes = set()
    for report in cases:
        for r in report["results"]:
            outcomes.add(r["outcome"])
            c = r["citation"]
            assert c["rule_id"] == r["rule_id"] and c["clause"] and c["url"].startswith("https://")
            assert c["edition"] == "NCC2025" and c["document"] and c["rule_status"] == "draft"
    assert outcomes == {"PASS", "FAIL", "NOT_APPLICABLE", "NEEDS_JUDGEMENT"}


def test_missing_input_is_needs_judgement_with_the_cause_named():
    r = one(run(RunRequest(vic25(), [Subject("s", [ECON25], econ_inputs(1000) | {})]), PACK))
    assert r["outcome"] == "PASS"
    inputs = econ_inputs(1000)
    del inputs["max_airside_component_airflow"]
    r = one(run(RunRequest(vic25(), [Subject("s", [ECON25], inputs)]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT"
    assert {"kind": "missing_input", "detail": "max_airside_component_airflow"} in r["causes"]


def test_unit_mismatch_is_needs_judgement_never_a_silent_cast():
    r = one(run_econ(1000, max_airside_component_airflow=iv(1000, "kg")))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"
    ok = one(run_econ(1000, max_airside_component_airflow=iv(3.6 * 1100, "m^3/hour")))   # 1100 L/s
    assert ok["outcome"] == "PASS"


def test_offset_temperature_units_refuse_to_convert():
    inputs = {"control_deadband": iv(1, "degC"), "specialised_application_smaller_range_claimed": iv(False),
              "system_type": iv("air_conditioning"), "is_electricity_substation": iv(False)}
    r = one(run(RunRequest(vic25(), [Subject("s", [DEAD25], inputs)]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"
    inputs["control_deadband"] = iv(3, "K")
    assert one(run(RunRequest(vic25(), [Subject("s", [DEAD25], inputs)]), PACK))["outcome"] == "PASS"


def test_unknown_enum_value_is_needs_judgement_not_silent_not_applicable():
    r = one(run_econ(5000, system_type=iv("AC")))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "domain"
    with pytest.raises(RunRefused) as exc:
        run_econ(5000, project=vic25(building_class="7A"))
    assert exc.value.code == "invalid_request"


def test_input_types_are_enforced():
    assert one(run_econ(5000, economy_cycle=iv(1)))["outcome"] == "NEEDS_JUDGEMENT"
    assert one(run_econ(5000, economy_cycle=iv("true")))["outcome"] == "NEEDS_JUDGEMENT"
    assert one(run_econ(float("nan")))["outcome"] == "NEEDS_JUDGEMENT"
    assert one(run_econ(float("inf")))["outcome"] == "NEEDS_JUDGEMENT"


def test_extracted_values_are_refused_before_any_evaluation():
    with pytest.raises(RunRefused) as exc:
        run_econ(1000, max_airside_component_airflow=iv(1000, "L/s", Provenance.EXTRACTED))
    assert exc.value.code == "extracted_inputs"
    assert "max_airside_component_airflow" in " ".join(exc.value.reasons)
    with pytest.raises(RunRefused):
        run(RunRequest(vic25(facts_provenance=Provenance.EXTRACTED), [Subject("s", [ECON25], econ_inputs(1))]), PACK)


def test_default_provenance_is_not_a_confirmed_value():
    r = one(run_econ(1000, max_airside_component_airflow=iv(1000, "L/s", Provenance.DEFAULT)))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unconfirmed"


def test_only_the_projects_edition_and_state_rules_are_selectable():
    other_edition = "NCC2022-J6D3-econ-cycle"
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(vic25(), [Subject("s", [other_edition], {})]), PACK)
    assert exc.value.code == "rule_not_selected"
    nsw_variant = "NCC2025-NSW-J6D3-time-switch-ac"
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(vic25(), [Subject("s", [nsw_variant], {})]), PACK)   # NSW rule on a VIC project
    assert exc.value.code == "rule_not_selected"
    with pytest.raises(RunRefused):
        run(RunRequest(vic25(), [Subject("s", ["NCC2025-DOES-NOT-EXIST"], {})]), PACK)


def test_jurisdiction_gate_refuses_unconfirmed_states():
    for state, edition in (("NT", "NCC2025"), ("NT", "NCC2022"), ("QLD", "NCC2025"), ("VIC", "NCC2022")):
        req = RunRequest(vic25(state=state, ncc_edition=edition), [Subject("s", [], {})])
        with pytest.raises(RunRefused) as exc:
            run(req, PACK)
        assert exc.value.code == "jurisdiction" and exc.value.reasons


def test_results_are_deterministic_and_ordered():
    a = run_econ(1250)
    b = run_econ(1250)
    assert a == b
    req = RunRequest(vic25(), [Subject("b", [ECON25, DEAD25], {}), Subject("a", [ECON25], {})])
    ids = [(r["subject_id"], r["rule_id"]) for r in run(req, PACK)["results"]]
    assert ids == [("b", ECON25), ("b", DEAD25), ("a", ECON25)]


def test_report_records_the_inputs_used_with_provenance():
    r = one(run_econ(1000))
    used = r["inputs_used"]["max_airside_component_airflow"]
    assert used == {"value": 1000, "unit": "L/s", "provenance": "engineer_confirmed"}
    assert r["inputs_used"]["climate_zone"]["provenance"] == "engineer_confirmed"


def test_outcome_enum_is_exactly_the_four_results():
    assert {o.value for o in Outcome} == {"PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE"}
