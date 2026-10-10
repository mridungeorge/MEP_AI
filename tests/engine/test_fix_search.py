"""Deterministic fix options: found by the rule engine's own evaluation, vetted by the cross-rule re-run, never invented."""
import copy
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from mep.diff.graph import build_graph
from mep.engine.cross_rule import cross_rule_rerun
from mep.engine.fix_search import candidate_fixes
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, Subject
from mep.engine.runner import _inputs_for

ROOT = Path(__file__).resolve().parents[2]
MIN_ID, CAP_ID, FLAG_ID, CAP15_ID = "NCC2025-J6D3-deadband-min", "NCC2025-J6D3-deadband-cap5", "NCC2025-J6D3-deadband-flag", "NCC2025-J6D3-deadband-cap15"
PROJECT = ProjectFacts("VIC", "NCC2025", 6, "5", date(2026, 10, 1))


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    rules = tmp_path_factory.mktemp("rules")
    (rules / "schema").mkdir()
    shutil.copy(ROOT / "rules/schema/rule.schema.json", rules / "schema" / "rule.schema.json")
    base = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    made = {}
    lo = copy.deepcopy(base)
    lo["id"] = MIN_ID
    made[MIN_ID] = lo
    for rid, cap in ((CAP_ID, 5), (CAP15_ID, 1.5)):
        hi = copy.deepcopy(base)
        hi["id"] = rid
        hi["threshold"]["values"] = {"max_deadband": {"value": cap, "unit": "K"}}
        hi["check"] = "control_deadband <= threshold('max_deadband')"
        hi["notes"] = "SYNTHETIC test rule: a cap on the dead band so that two rules read one input."
        made[rid] = hi
    flag = copy.deepcopy(base)
    flag["id"] = FLAG_ID
    flag["inputs"] = [*flag["inputs"], {"name": "has_override", "type": "bool", "provenance": "engineer_confirmed"}]
    flag["check"] = "has_override == True"
    flag["depends_on"] = [*flag["depends_on"], "systems.has_override"]
    flag["notes"] = "SYNTHETIC test rule: a yes/no input."
    made[FLAG_ID] = flag
    for name, rule in made.items():
        (rules / f"{name}.yaml").write_text(yaml.safe_dump(rule, sort_keys=False), encoding="utf-8")
    return load_pack(rules)


def c(v, u=None, prov=Provenance.ENGINEER_CONFIRMED):
    return InputValue(v, u, prov)


def values(deadband=1.0, override=False):
    return {"control_deadband": c(deadband, "K"), "specialised_application_smaller_range_claimed": c(False), "system_type": c("air_conditioning"),
            "is_electricity_substation": c(False), "has_override": c(override)}


def full(pack, rule_id, deadband=1.0, override=False):
    """The inputs as the runner hands them to a rule (project facts added)."""
    return _inputs_for(Subject("ahu", [rule_id], values(deadband, override)), PROJECT, pack.rules[rule_id])


def test_a_number_option_is_the_boundary_value_that_passes(pack):
    opts = candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.0))
    assert [(o.input_name, o.kind, o.to_value, o.unit) for o in opts] == [("control_deadband", "number", 2.0, "K")]
    assert opts[0].label.startswith("Hypothesis: verify.") and "PASS" in opts[0].label


def test_the_boundary_is_found_to_three_figures_on_the_passing_side(pack):
    low = candidate_fixes(pack.rules[CAP_ID], full(pack, CAP_ID, 6.3))               # the cap is 5: the nearest passing value is 5 itself
    assert low and low[0].to_value == 5.0
    assert candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.999))[0].to_value == 2.0


def test_a_yes_no_input_offers_the_other_answer(pack):
    opts = candidate_fixes(pack.rules[FLAG_ID], full(pack, FLAG_ID, override=False))
    assert [(o.input_name, o.kind, o.from_value, o.to_value) for o in opts] == [("has_override", "yes_no", False, True)]


def test_nothing_is_offered_for_a_pass_or_for_inputs_the_engineer_does_not_decide(pack):
    assert candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 3.0)) == []
    for o in candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.0)):
        assert o.input_name not in ("system_type", "climate_zone", "building_class")


def test_options_are_deterministic_and_have_stable_ids(pack):
    a, b = candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.0)), candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.0))
    assert a == b and a[0].id == b[0].id and len(a[0].id) == 16


def test_the_cross_rule_rerun_withdraws_an_option_that_breaks_another_rule(pack):
    graph = build_graph(pack)
    # 1.4 K passes the 1.5 K cap and fails the 2 K minimum: raising it to 2.0 fixes the minimum and breaks the cap
    s2 = Subject("ahu-2", [MIN_ID, CAP15_ID], values(1.4))
    o2 = candidate_fixes(pack.rules[MIN_ID], full(pack, MIN_ID, 1.4))
    r2 = cross_rule_rerun(subject=s2, project=PROJECT, changes={"control_deadband": c(o2[0].to_value, "K")}, pack=pack, graph=graph, target_rule=MIN_ID)
    assert r2.withdrawn and r2.conflicts and r2.conflicts[0].worse.rule_id == CAP15_ID
    s3 = Subject("ahu-3", [MIN_ID, CAP_ID], values(1.0))                              # the 5 K cap is happy with 2 K: accepted
    r3 = cross_rule_rerun(subject=s3, project=PROJECT, changes={"control_deadband": c(2.0, "K")}, pack=pack, graph=graph, target_rule=MIN_ID)
    assert not r3.withdrawn and not r3.conflicts


def test_the_option_id_depends_on_the_starting_value():
    from mep.engine.fix_search import FixOption
    assert FixOption("R", "x", 80, 100, "L/s", "number").id != FixOption("R", "x", 140, 100, "L/s", "number").id
