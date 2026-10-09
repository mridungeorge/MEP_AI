"""Run every draft rule's own test cases, plus a generic unit-mismatch mutation (rule 8)."""
from pathlib import Path

import pytest
import yaml

from tests.rules.rule_runner import NA, NJ, Missing, Rule

ROOT = Path(__file__).resolve().parents[2]
RULES = {
    p.stem: yaml.safe_load(p.read_text(encoding="utf-8"))
    for p in sorted((ROOT / "rules").rglob("*.yaml"))
    if "schema" not in p.parts and p.name not in ("adoption.yaml", "applicability.yaml")
}
CASES = [(rid, t["name"]) for rid, r in RULES.items() for t in r["tests"]]


@pytest.mark.parametrize(("rule_id", "name"), CASES)
def test_rule_case(rule_id, name):
    rule = RULES[rule_id]
    case = next(t for t in rule["tests"] if t["name"] == name)
    assert Rule(rule).run(case["inputs"]) == case["expect"]


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_unit_mismatch_is_needs_judgement(rule_id):
    """A numeric input given in a unit of the wrong dimension must never yield PASS/FAIL."""
    rule = RULES[rule_id]
    numeric = [i["name"] for i in rule["inputs"] if i.get("unit")]
    if not numeric:
        pytest.skip("no unit-bearing inputs")
    runner = Rule(rule)
    for case in rule["tests"]:
        hit = [n for n in numeric if n in case["inputs"]]
        if hit and case["expect"] in ("PASS", "FAIL"):
            bad = dict(case["inputs"])
            bad[hit[0]] = {"value": 1, "unit": "kg"}
            assert runner.run(bad) == NJ
            return
    pytest.skip("no PASS/FAIL case with a numeric input")


def _sole_cause(rule, kind, expr, expect):
    """True if some test case yields `expect` BECAUSE of this expression: it is true there, and with
    the expression removed the result changes (so it is not masked by another cause)."""
    runner = Rule(rule)
    reduced = {**rule, "applies_when": {**rule["applies_when"],
                                         kind: [e for e in rule["applies_when"][kind] if e != expr]}}
    for case in rule["tests"]:
        try:
            fired = runner.evaluate(expr, case["inputs"])
        except Missing:
            continue
        if fired and runner.run(case["inputs"]) == expect and Rule(reduced).run(case["inputs"]) != expect:
            return True
    return False


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_every_exemption_path_is_the_sole_cause_in_some_test(rule_id):
    """A typo in an exemption must fail CI: each exempt_when / needs_judgement_when entry alone
    decides a NOT_APPLICABLE / NEEDS_JUDGEMENT result in at least one test."""
    rule = RULES[rule_id]
    for kind, expect in (("exempt_when", NA), ("needs_judgement_when", NJ)):
        for expr in rule["applies_when"].get(kind, []):
            assert _sole_cause(rule, kind, expr, expect), f"untested or masked {kind}: {expr}"


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_filters_are_exercised_both_ways(rule_id):
    """Each applies_when filter is matched by a PASS/FAIL test and breaks (NOT_APPLICABLE) in another."""
    rule = RULES[rule_id]
    runner = Rule(rule)
    base = next(t for t in rule["tests"] if t["expect"] in ("PASS", "FAIL"))
    for key, spec in rule["applies_when"].items():
        if key in ("edition", "state", "exempt_when", "needs_judgement_when"):
            continue
        domain = next(i["values"] for i in rule["inputs"] if i["name"] == key) if any(
            i["name"] == key and i.get("values") for i in rule["inputs"]) else None
        allowed = spec if isinstance(spec, list) else (spec["in"] if isinstance(spec, dict) and "in" in spec else [spec])
        outside = [v for v in (domain or []) if v not in allowed]
        if isinstance(spec, dict) and "not_in" in spec:
            outside = list(spec["not_in"])
        if not outside:
            continue
        case = dict(base["inputs"])
        case[key] = outside[0]
        assert runner.run(case) == NA, f"{key}={outside[0]!r} should be NOT_APPLICABLE"


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_unknown_enum_value_is_needs_judgement_not_silent_na(rule_id):
    rule = RULES[rule_id]
    runner = Rule(rule)
    base = next(t for t in rule["tests"] if t["expect"] in ("PASS", "FAIL"))
    for spec in rule["inputs"]:
        if spec.get("values") and spec["name"] in base["inputs"]:
            for bad in (5, "7A", "not_a_value"):
                case = dict(base["inputs"])
                case[spec["name"]] = bad
                assert runner.run(case) == NJ, (spec["name"], bad)


OFFSET_CASES = [
    ("NCC2022-J6D3-deadband", "control_deadband", {"value": 1, "unit": "degC"}),
    ("NCC2025-J6D3-deadband", "control_deadband", {"value": 1, "unit": "degC"}),
    ("NCC2025-J6D3-deadband", "control_deadband", {"value": 1, "unit": "degF"}),
    ("NCC2022-J6D9-pipe-insulation", "fluid_temp", {"value": 6, "unit": "delta_degC"}),
    ("NCC2022-J6D9-pipe-insulation", "fluid_temp", {"value": 6, "unit": "kelvin"}),
    ("NCC2025-J6D9-pipe-insulation", "fluid_temperature", {"value": 43, "unit": "degF"}),
]


@pytest.mark.parametrize(("rule_id", "name", "bad"), OFFSET_CASES)
def test_offset_temperature_units_never_convert_silently(rule_id, name, bad):
    rule = RULES[rule_id]
    base = next(t for t in rule["tests"] if t["expect"] in ("PASS", "FAIL") and name in t["inputs"])
    case = dict(base["inputs"])
    case[name] = bad
    assert Rule(rule).run(case) == NJ


def test_same_dimension_multiplicative_units_do_convert():
    rule = RULES["NCC2025-J6D3-econ-cycle"]
    base = next(t for t in rule["tests"] if t["expect"] == "FAIL")
    case = dict(base["inputs"])
    case["max_airside_component_airflow"] = {"value": 9000 * 3.6, "unit": "m^3/hour"}  # 9000 L/s
    assert Rule(rule).run(case) == base["expect"]
