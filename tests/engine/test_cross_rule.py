"""Cross-rule re-run: a change that fixes one rule and breaks another is reported as a conflict naming both rules and the input."""
import copy
import shutil
from datetime import date
from pathlib import Path

import pytest
import yaml
from mep.diff.graph import build_graph
from mep.engine.cross_rule import cross_rule_rerun, vet_fix_hypotheses
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, Outcome, ProjectFacts, Provenance, Subject

ROOT = Path(__file__).resolve().parents[2]
MIN_ID, MAX_ID = "NCC2025-J6D3-deadband-min", "NCC2025-J6D3-deadband-max"


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    """The real deadband rule (>= 2 K) plus a SYNTHETIC copy that caps the dead band (<= 5 K), reading the same input."""
    rules = tmp_path_factory.mktemp("rules")
    (rules / "schema").mkdir()
    shutil.copy(ROOT / "rules/schema/rule.schema.json", rules / "schema" / "rule.schema.json")
    base = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml").read_text(encoding="utf-8"))
    lo = copy.deepcopy(base)
    lo["id"] = MIN_ID
    hi = copy.deepcopy(base)
    hi["id"] = MAX_ID
    hi["threshold"]["values"] = {"max_deadband": {"value": 5, "unit": "K"}}
    hi["check"] = "control_deadband <= threshold('max_deadband')"
    hi["notes"] = "SYNTHETIC test rule: a cap on the dead band so that two rules read one input."
    for name, rule in ((MIN_ID, lo), (MAX_ID, hi)):
        (rules / f"{name}.yaml").write_text(yaml.safe_dump(rule, sort_keys=False), encoding="utf-8")
    return load_pack(rules)


def inputs(deadband):
    def c(v, u=None):
        return InputValue(v, u, Provenance.ENGINEER_CONFIRMED)

    return {"control_deadband": c(deadband, "K"), "specialised_application_smaller_range_claimed": c(False),
            "system_type": c("air_conditioning"), "is_electricity_substation": c(False)}


def project():
    return ProjectFacts("VIC", "NCC2025", 6, "5", date(2026, 10, 1))


def subject(deadband):
    return Subject("ahu-1", [MIN_ID, MAX_ID], inputs(deadband))


def change(v):
    return {"control_deadband": InputValue(v, "K", Provenance.ENGINEER_CONFIRMED)}


def test_a_fix_that_satisfies_every_dependent_rule_is_kept(pack):
    r = cross_rule_rerun(subject=subject(1), project=project(), changes=change(3), pack=pack, graph=build_graph(pack),
                         target_rule=MIN_ID)
    assert r.dependents == [MAX_ID, MIN_ID] and not r.conflicts and not r.withdrawn
    assert [(m.rule_id, m.before, m.after) for m in r.moves] == [(MIN_ID, Outcome.FAIL, Outcome.PASS)]
    assert vet_fix_hypotheses(["Hypothesis: verify. raise it"], r) == ["Hypothesis: verify. raise it"]


def test_a_fix_that_breaks_another_rule_is_withdrawn_with_the_conflict_named(pack):
    r = cross_rule_rerun(subject=subject(1), project=project(), changes=change(8), pack=pack, graph=build_graph(pack),
                         target_rule=MIN_ID)
    assert r.withdrawn and len(r.conflicts) == 1
    c = r.conflicts[0]
    assert (c.input_name, c.better.rule_id, c.worse.rule_id) == ("control_deadband", MIN_ID, MAX_ID)
    assert (c.worse.before, c.worse.after) == (Outcome.PASS, Outcome.FAIL)
    assert c.describe() == (f"ahu-1: changing control_deadband moves {MIN_ID} FAIL -> PASS but breaks {MAX_ID} PASS -> FAIL")
    assert vet_fix_hypotheses(["Hypothesis: verify. raise it"], r) == []


def test_a_fix_that_does_not_make_its_target_pass_is_withdrawn(pack):
    r = cross_rule_rerun(subject=subject(1), project=project(), changes=change(1.5), pack=pack, graph=build_graph(pack),
                         target_rule=MIN_ID)
    assert r.withdrawn and not r.conflicts


def test_without_a_target_a_better_and_a_worse_rule_on_one_input_conflict(pack):
    r = cross_rule_rerun(subject=subject(1), project=project(), changes=change(8), pack=pack, graph=build_graph(pack))
    assert [(c.better.rule_id, c.worse.rule_id, c.input_name) for c in r.conflicts] == [(MIN_ID, MAX_ID, "control_deadband")]
    plain = cross_rule_rerun(subject=subject(1), project=project(), changes=change(3), pack=pack, graph=build_graph(pack))
    assert not plain.conflicts and not plain.withdrawn


def test_a_change_that_only_worsens_is_a_move_not_a_conflict(pack):
    r = cross_rule_rerun(subject=subject(3), project=project(), changes=change(0.5), pack=pack, graph=build_graph(pack))
    assert [(m.rule_id, m.after) for m in r.moves] == [(MIN_ID, Outcome.FAIL)] and not r.conflicts


def test_an_input_no_assigned_rule_reads_changes_nothing(pack):
    r = cross_rule_rerun(subject=subject(3), project=project(), pack=pack, graph=build_graph(pack),
                         changes={"duct_insulation_r_value": InputValue(2, "m^2.K/W", Provenance.ENGINEER_CONFIRMED)})
    assert r.dependents == [] and r.moves == [] and not r.conflicts
