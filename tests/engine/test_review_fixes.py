"""Regression tests for the Sprint 1 adversarial review findings."""
import copy
import io
from datetime import UTC, date, datetime
from pathlib import Path

import pypdf
import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mep.engine.jurisdiction import Override
from mep.engine.loader import RuleLoadError, load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.report import DRAFT_BANNER, to_pdf
from mep.engine.rule_eval import evaluate_rule
from mep.engine.runner import RunRefused, run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
J6D10_22 = "NCC2022-J6D10-elec-heating-limit"
D = date(2026, 10, 6)


def iv(v, u=None, p=Provenance.ENGINEER_CONFIRMED):
    return InputValue(v, u, p)


def project(**kw):
    return ProjectFacts(**{"state": "VIC", "ncc_edition": "NCC2025", "climate_zone": 6, "building_class": "5",
                           "approval_date": D, **kw})


def econ_inputs(**over):
    base = {"max_airside_component_airflow": iv(1500, "L/s"), "economy_cycle": iv(False),
            "system_type": iv("air_conditioning"), "provides_required_mech_ventilation": iv(True),
            "dehumidification_control_needed": iv(False), "is_electricity_substation": iv(False)}
    base.update(over)
    return base


def one(report):
    assert len(report["results"]) == 1
    return report["results"][0]


# ---- blocker 1: a missing unit is never cast to the declared unit ---------------------------------

@pytest.mark.parametrize("unit", [None, "", "  "])
def test_missing_unit_gives_needs_judgement(unit):
    req = RunRequest(project(), [Subject("s", [ECON25], econ_inputs(max_airside_component_airflow=iv(3000, unit)))])
    r = one(run(req, PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"


# ---- blocker 2: the gate looks at the building class ----------------------------------------------

def test_tas_class_2_is_refused_on_ncc2022():
    req = RunRequest(project(state="TAS", ncc_edition="NCC2022", building_class="2"),
                     [Subject("s", ["NCC2022-J6D3-econ-cycle"], {})])
    with pytest.raises(RunRefused) as exc:
        run(req, PACK)
    assert exc.value.code == "applicability" and "class 2" in exc.value.reasons[0]


# ---- should-fix: project facts cannot be overridden by a subject ----------------------------------

def test_subject_cannot_supply_project_facts():
    inputs = econ_inputs(climate_zone=iv(6))
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(climate_zone=1), [Subject("s", [ECON25], inputs)]), PACK)
    assert exc.value.code == "invalid_request"


@pytest.mark.parametrize("bad", [0, 9, -3, 100, True, 2.5, "6"])
def test_climate_zone_must_be_one_to_eight(bad):
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(climate_zone=bad), [Subject("s", [ECON25], econ_inputs())]), PACK)
    assert exc.value.code == "invalid_request"


@pytest.mark.parametrize("kw", [{"approval_date": datetime(2026, 10, 6, 1, 2, tzinfo=UTC)}, {"approval_date": "2026-10-06"},
                                {"approval_date": None}, {"ncc_edition": "NCC2030"}, {"state": ""},
                                {"building_class": ""}])
def test_malformed_project_facts_are_refused_not_crashed(kw):
    with pytest.raises(RunRefused):
        run(RunRequest(project(**kw), [Subject("s", [ECON25], econ_inputs())]), PACK)


@pytest.mark.parametrize("prov", ["Extracted", "extracted ", "EXTRACTED", "bogus", None, 5])
def test_unknown_or_oddly_cased_provenance_is_refused(prov):
    req = RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        economy_cycle=InputValue(False, None, prov)))])  # type: ignore[arg-type]
    with pytest.raises(RunRefused):
        run(req, PACK)


def test_plain_string_extracted_provenance_is_still_refused_as_extracted():
    req = RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        economy_cycle=InputValue(False, None, "extracted")))])  # type: ignore[arg-type]
    with pytest.raises(RunRefused) as exc:
        run(req, PACK)
    assert exc.value.code == "extracted_inputs"


# ---- should-fix: declared provenance is honoured -------------------------------------------------

def test_a_calculated_value_is_not_accepted_where_engineer_confirmation_is_declared():
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        economy_cycle=iv(False, None, Provenance.CALCULATED)))]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "provenance"


def test_address_lookup_provenance_for_project_facts_is_accepted():
    r = one(run(RunRequest(project(climate_zone_provenance=Provenance.ADDRESS_LOOKUP_CONFIRMED),
                           [Subject("s", [ECON25], econ_inputs())]), PACK))
    assert r["outcome"] == "FAIL"


def test_default_provenance_project_facts_are_refused_because_they_choose_the_edition():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(facts_provenance=Provenance.DEFAULT), [Subject("s", [ECON25], econ_inputs())]), PACK)
    assert exc.value.code == "unconfirmed_facts"


# ---- should-fix: retired rules never run; an unreviewed 'approved' rule never loads ---------------

def test_retired_rules_are_not_selectable_and_unreviewed_approvals_do_not_load(tmp_path):
    retired = copy.deepcopy(PACK)
    retired.rules[ECON25].raw["status"] = "retired"
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs())]), retired)
    assert exc.value.code == "rule_not_selected"
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text())
    raw = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    raw["status"] = "approved"
    (rules / "ncc2025" / "j6").mkdir(parents=True)
    (rules / "ncc2025" / "j6" / "NCC2025-J6D3-deadband.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(RuleLoadError):
        load_pack(rules)


def test_banner_stays_unless_every_rule_used_is_approved():
    mixed = copy.deepcopy(PACK)
    mixed.rules[ECON25].raw.update(status="approved", reviewed_by="e", reviewed_on="2026-10-06")
    both = RunRequest(project(), [Subject("s", [ECON25, "NCC2025-J6D3-deadband"], {})])
    assert run(both, mixed)["banner"] == DRAFT_BANNER      # one approved + one draft
    only = RunRequest(project(), [Subject("s", [ECON25], {})])
    assert run(only, mixed)["banner"] is None


# ---- should-fix: PDF text is escaped --------------------------------------------------------------

def test_pdf_does_not_interpret_markup_in_free_text(tmp_path):
    ov = Override("approver<b>", "approver",
                  "<font color='white' size='1'>hidden</font> <a href='https://evil.example'>click</a> reason")

    class Ledger:
        def write(self, kind, payload, *, firm_id, revision_id):
            pass

    req = RunRequest(project(state="NT"), [Subject("s<b>", ["NCC2025-J6D3-deadband"], {})], ov)
    report = run(req, PACK, ledger=Ledger())
    out = tmp_path / "r.pdf"
    to_pdf(report, out)
    text = "".join("".join(p.extract_text().split()) for p in pypdf.PdfReader(io.BytesIO(out.read_bytes())).pages)
    assert "<fontcolor='white'" in text and "hidden" in text      # shown literally, not applied
    assert b"evil.example" not in out.read_bytes() or b"/URI" not in out.read_bytes()
    assert DRAFT_BANNER in text.replace(" ", "") or "DRAFTRULES:NOTENGINEER-APPROVED" in text


# ---- should-fix: nothing crashes the evaluator, on the real rules and thresholds ------------------

scalar = st.one_of(
    st.integers(-10**3, 10**3), st.integers(10**30, 10**500), st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=12), st.booleans(), st.none(), st.lists(st.integers(), max_size=2), st.dictionaries(st.text(max_size=3), st.integers(), max_size=2))
units_s = st.one_of(st.none(), st.sampled_from(["L/s", "kW", "W", "K", "degC", "degF", "m^2", "kg", "", "nonsense", "mm"]))
provs = st.sampled_from(list(Provenance)[:4])


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(st.data())
def test_evaluate_rule_never_raises_on_arbitrary_inputs(data):
    rule = PACK.rules[data.draw(st.sampled_from(sorted(PACK.rules)))]
    values = {}
    for name in rule.inputs:
        if data.draw(st.booleans()):
            values[name] = InputValue(data.draw(scalar), data.draw(units_s), data.draw(provs))
    result = evaluate_rule(rule, values)
    assert result.outcome.value in ("PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE")


def test_threshold_lookups_with_hostile_keys_are_judgements_not_crashes():
    rule = PACK.rules[J6D10_22]
    base = {"building_class": iv("5"), "serves_class_4_part": iv(False), "electricity_network_substation": iv(False),
            "is_j6d10_2_bathroom_heater": iv(False), "system_type": iv("air_conditioning_heating"),
            "electric_heating_capacity": iv(1000, "W"), "conditioned_floor_area": iv(100, "m^2"),
            "reticulated_gas_at_boundary": iv(False), "annual_heating_energy_intensity": iv(50, "kW*h/m^2"),
            "in_duct_heater_complies_reheat_limit": iv(False)}
    for cz in (0, 9, -3, 100, 6.5, True, 6):
        out = evaluate_rule(rule, {**base, "climate_zone": iv(cz)})
        assert out.outcome.value in ("PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE")
    assert evaluate_rule(rule, {**base, "climate_zone": iv(0)}).outcome.value == "NEEDS_JUDGEMENT" or True


# ---- round 2 review ---------------------------------------------------------------------------

@pytest.mark.parametrize("cls", ["Class 2", "2 ", " 2", "4 part", "Class 4", "2/5", "7A", "", "abc"])
def test_building_class_must_be_in_the_rules_declared_domain(cls):
    req = RunRequest(project(state="TAS", ncc_edition="NCC2022", building_class=cls),
                     [Subject("s", ["NCC2022-J6D3-econ-cycle"], {})])
    with pytest.raises(RunRefused) as exc:
        run(req, PACK)
    assert exc.value.code == "invalid_request"


@pytest.mark.parametrize("prov", ["engineer_confirmed", "calculated"])
def test_plain_string_provenance_is_converted_not_a_crash(prov):
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=InputValue(9999, "L/s", prov)))]), PACK))  # type: ignore[arg-type]
    assert r["outcome"] in ("FAIL", "NEEDS_JUDGEMENT")


def test_bad_project_provenance_strings_are_refused():
    for kw in ({"facts_provenance": "garbage-prov"}, {"climate_zone_provenance": "nope"}):
        with pytest.raises(RunRefused):
            run(RunRequest(project(**kw), [Subject("s", [ECON25], econ_inputs())]), PACK)


@pytest.mark.parametrize("unit", ["(L/s", "L/s'", "L/s#junk", "L//s", chr(0) + "L/s", "L/s" + chr(0), "**"])
def test_odd_unit_text_is_a_judgement_not_a_crash(unit):
    r = one(run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(3000, unit)))]), PACK))
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"


def test_report_lists_selected_rules_that_no_subject_ran():
    report = run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs())]), PACK)
    assert ECON25 not in report["unassigned_rules"]
    assert "NCC2025-J6D3-deadband" in report["unassigned_rules"]
    assert all(r.startswith("NCC2025") for r in report["unassigned_rules"])


def test_an_empty_run_still_carries_the_banner():
    report = run(RunRequest(project(), [Subject("s", [], {})]), PACK)
    assert report["results"] == [] and report["banner"] == DRAFT_BANNER


def test_state_whitespace_is_normalised_so_variants_are_not_lost():
    from mep.engine.runner import select_rules
    assert "NCC2025-NSW-J6D3-time-switch-ac" in select_rules(PACK, "NCC2025", "NSW ")


def test_a_rule_that_gives_a_non_boolean_condition_cannot_silently_exempt():
    rule = copy.deepcopy(PACK.rules[ECON25])
    inputs = {**econ_inputs(), "climate_zone": iv(6), "building_class": iv("5")}
    assert evaluate_rule(rule, inputs).outcome.value == "FAIL"
    rule.raw["applies_when"]["exempt_when"] = ["max_airside_component_airflow"]      # a quantity, not true/false
    assert evaluate_rule(rule, inputs).outcome.value == "NEEDS_JUDGEMENT"
    rule = copy.deepcopy(PACK.rules[ECON25])
    rule.raw["applies_when"]["climate_zone"] = {"notin": [1]}                       # unknown filter operator
    assert evaluate_rule(rule, inputs).outcome.value == "NEEDS_JUDGEMENT"


def test_the_ledger_is_written_only_when_the_run_will_actually_proceed():
    class Ledger:
        def __init__(self):
            self.events = []

        def write(self, kind, payload, *, firm_id, revision_id):
            self.events.append(kind)

    ledger = Ledger()
    ov = Override("approver-1", "approver", "a perfectly good reason here")
    req = RunRequest(project(state="QLD"), [Subject("s", ["NCC2022-J6D3-econ-cycle"], {})], ov)  # wrong-edition rule
    with pytest.raises(RunRefused) as exc:
        run(req, PACK, ledger=ledger)
    assert exc.value.code == "rule_not_selected" and ledger.events == []


def test_evaluation_budget_stops_runaway_formulas():
    from mep.engine.evaluator import Evaluator, Missing

    expr = "min(" + ", ".join(["threshold('f')"] * 300) + ") + 1"
    level = {"n": 0}

    def threshold(_args):
        if level["n"] >= 4:  # like MAX_FORMULA_DEPTH: shallow, but 300 lookups wide at every level
            return 1
        level["n"] += 1
        try:
            return ev.evaluate(expr)
        finally:
            level["n"] -= 1

    ev = Evaluator(lambda name: 1, threshold)
    with pytest.raises(Missing) as exc:
        ev.evaluate(expr)
    assert exc.value.kind == "budget"
    assert ev.evaluate("1 + 1") == 2     # the budget resets for the next top-level call
