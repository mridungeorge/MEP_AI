import json
import sys
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import validate_rules

VALIDATOR = Draft202012Validator(json.loads((ROOT / "rules/schema/rule.schema.json").read_text()))
GOOD = ROOT / "rules/ncc2022/j6/NCC2022-J6D3-econ-cycle.yaml"


def run(tmp_path, mutate, ed="ncc2022"):
    rule = yaml.safe_load(GOOD.read_text())
    mutate(rule)
    d = tmp_path / "rules" / ed / "j6"
    d.mkdir(parents=True)
    f = d / f"{rule['id']}.yaml"
    f.write_text(yaml.safe_dump(rule))
    return validate_rules.check_file(f, VALIDATOR)


def test_good_rule_valid(tmp_path):
    assert run(tmp_path, lambda r: None) == []


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(status="approved"),
    lambda r: r.update(clause_text="copied wording"),
    lambda r: r.update(tests=r["tests"][:1]),
    lambda r: r.pop("depends_on"),
    lambda r: r["outcomes"].update(missing_input="PASS"),
    lambda r: r["threshold"].update(clause_text="copied wording"),
    lambda r: r["outcomes"].update({"true": "PASS", "false": "PASS"}),
    lambda r: r.update(check="__import__('os').system('x')"),
    lambda r: r.update(check="undeclared_name > 1"),
    lambda r: r.update(check="threshold(climate_zone)[0] > 1"),
    lambda r: r["tests"][0]["inputs"].update(bogus=1),
    lambda r: r.update(fix_hypotheses=["Add an economy cycle"]),
    lambda r: r["applies_when"].update(edition="NCC2025"),
    lambda r: r["source"].update(edition="2025"),
    lambda r: r["inputs"][0].pop("unit"),
    lambda r: r.update(depends_on=["x.climate_zone_x"]),
    lambda r: r.update(id="NCC2022-NSW-J6D3-econ-cycle"),
    lambda r: r["threshold"]["values"]["by_climate_zone"].update(unit="per value"),
    lambda r: r["threshold"]["values"]["by_climate_zone"].pop("unit"),
    lambda r: r["threshold"]["values"].update(bare=5),
    lambda r: r.update(check="airside_component_airflow < 2000 or economy_cycle == True"),
    lambda r: r.update(check="airside_component_airflow < threshold('by_climate_zone', 99) or economy_cycle == True"),
    lambda r: r["threshold"]["values"].update(f={"formula": "airside_component_airflow * 2", "unit": "W"}),
    lambda r: r["applies_when"].update(undeclared_key="x"),
    lambda r: r["applies_when"].update(economy_cycle=True),
    lambda r: r["applies_when"].update(climate_zone={"le": 3}),
    lambda r: (r["applies_when"].update(exempt_when=["economy_cycle == True"]),
               r.update(tests=[t for t in r["tests"] if t["expect"] != "NOT_APPLICABLE"])),
    lambda r: (r["applies_when"].update(needs_judgement_when=["economy_cycle == True"]),
               r.update(tests=[t for t in r["tests"] if t["expect"] != "NEEDS_JUDGEMENT"])),
    lambda r: r["inputs"][0].update(unit="furlongs_per_fortnight_squared_x"),
    lambda r: r.update(check="airside_component_airflow in [2000, 3000]"),
    lambda r: r.update(check="airside_component_airflow < '2000'"),
    lambda r: r.update(check="airside_component_airflow"),
    lambda r: r.update(check="airside_component_airflow - threshold('by_climate_zone', climate_zone)"),
    lambda r: r.update(check="economy_cycle == 1"),
    lambda r: r.update(check="threshold('by_climate_zone') > 1 or economy_cycle == True"),
    lambda r: r.update(check="airside_component_airflow < threshold('by_climate_zone', 2, 3) or economy_cycle == True"),
    lambda r: r["threshold"]["values"].update(f={"formula": "True", "unit": "L/s"}),
    lambda r: r["threshold"]["values"].update(f={"formula": "threshold('f')", "unit": "L/s"}),
    lambda r: r["threshold"]["values"].update(zero={"value": 3, "unit": "dimensionless"}),
    lambda r: r["applies_when"].update(economy_cycle=False),
    lambda r: r["applies_when"].update(economy_cycle={"in": [False]}),
    lambda r: r["applies_when"].update(airside_component_airflow=5),
    lambda r: r["source"].update(edition=""),
    lambda r: r.update(reviewed_on="whenever"),
    lambda r: r["inputs"].append({"name": "building_class", "type": "string", "provenance": "engineer_confirmed"})
    or r["depends_on"].append("project.building_class") or r["applies_when"].update(building_class=["5"]),
    lambda r: r.update(check="[airside_component_airflow >= threshold('by_climate_zone', 2)]"),
    lambda r: r.update(check="True"),
    lambda r: r.update(check="1 < 2"),
    lambda r: r.update(check="not [economy_cycle]"),
    lambda r: r.update(check="'air' in system_type"),
    lambda r: r.update(check="climate_zone >= 0 in [3]"),
    lambda r: r["inputs"].append({"name": "pct", "type": "number", "unit": "percent", "provenance": "engineer_confirmed"})
    or r["depends_on"].append("x.pct") or r.update(check="pct >= 50 or economy_cycle == True"),
    lambda r: r["applies_when"].update(system_type=["air_conditioning", 5]),
    lambda r: r["inputs"].append({"name": "kind", "type": "enum", "provenance": "engineer_confirmed"})
    or r["depends_on"].append("x.kind") or r.update(check="kind == 'a' or economy_cycle == True"),
    lambda r: r.update(check="airside_component_airflow < threshold('by_climate_zone', climate_zone) or "
                             "min(economy_cycle) == True"),
    lambda r: r["inputs"].append({"name": "mode", "type": "enum", "values": ["a", "b"],
                                  "provenance": "engineer_confirmed"})
    or r["depends_on"].append("x.mode") or r.update(check="mode == 'c' or economy_cycle == True"),
    lambda r: r["inputs"].append({"name": "mode", "type": "enum", "values": ["a", "b"],
                                  "provenance": "engineer_confirmed"})
    or r["depends_on"].append("x.mode") or r.update(check="mode in ['a', 'bb'] or economy_cycle == True"),
])
def test_bad_rules_rejected(tmp_path, mutate):
    assert run(tmp_path, mutate)


def test_wrong_edition_directory_rejected(tmp_path):
    assert run(tmp_path, lambda r: None, ed="ncc2025")


def test_offset_temperature_units_must_match_between_input_and_threshold(tmp_path):
    def mutate(r):
        r["inputs"][0] = {"name": "t_in", "type": "number", "unit": "K", "provenance": "engineer_confirmed"}
        r["depends_on"] = ["x.t_in", *[d for d in r["depends_on"] if not d.endswith("airside_component_airflow")]]
        r["threshold"]["values"] = {"limit": {"value": 2, "unit": "degC"}}
        r["check"] = "t_in >= threshold('limit') or economy_cycle == True"
        for t in r["tests"]:
            t["inputs"] = {k: v for k, v in t["inputs"].items() if k != "airside_component_airflow"}
    errors = run(tmp_path, mutate)
    assert any("offset temperature units" in e for e in errors), errors


def test_wrapped_offset_temperature_comparison_is_rejected(tmp_path):
    def mutate(r):
        r["inputs"][0] = {"name": "t_in", "type": "number", "unit": "degC", "provenance": "engineer_confirmed"}
        r["depends_on"] = ["x.t_in", *[d for d in r["depends_on"] if not d.endswith("airside_component_airflow")]]
        r["threshold"]["values"] = {"limit": {"value": 2, "unit": "K"}}
        r["check"] = "t_in >= max(threshold('limit'), threshold('limit')) or economy_cycle == True"
        for t in r["tests"]:
            t["inputs"] = {k: v for k, v in t["inputs"].items() if k != "airside_component_airflow"}
    assert any("offset temperature units" in e for e in run(tmp_path, mutate))


@pytest.mark.parametrize("check", [
    "t_in - threshold('limit') >= threshold('limit') or economy_cycle == True",
    "abs(t_in) >= threshold('limit') or economy_cycle == True",
    "-t_in >= threshold('limit') or economy_cycle == True",
    "t_in in [threshold('limit')] or economy_cycle == True",
    "t_in >= max(threshold('limit'), threshold('limit')) or economy_cycle == True",
])
def test_absolute_temperatures_never_enter_arithmetic_or_functions(tmp_path, check):
    def mutate(r):
        r["inputs"][0] = {"name": "t_in", "type": "number", "unit": "degC", "provenance": "engineer_confirmed"}
        r["depends_on"] = ["x.t_in", *[d for d in r["depends_on"] if not d.endswith("airside_component_airflow")]]
        r["threshold"]["values"] = {"limit": {"value": 2, "unit": "degC"}}
        r["check"] = check
        for t in r["tests"]:
            t["inputs"] = {k: v for k, v in t["inputs"].items() if k != "airside_component_airflow"}
    assert any("absolute temperatures" in e or "offset temperature" in e for e in run(tmp_path, mutate)), check


def test_formula_cannot_be_an_absolute_temperature(tmp_path):
    def mutate(r):
        r["threshold"]["values"]["f"] = {"formula": "airside_component_airflow", "unit": "degC"}
    assert run(tmp_path, mutate)


@pytest.mark.parametrize("unit", ["L/s*9^9_999_999", "K*9^9_999_999_999", "L/s^(9^9^9)", "L/s*1"])
def test_unit_text_that_could_hang_pint_is_rejected_quickly(tmp_path, unit):
    import time

    def mutate(r):
        r["inputs"][0]["unit"] = unit
        r["threshold"]["values"]["by_climate_zone"]["unit"] = unit
    start = time.monotonic()
    errors = run(tmp_path, mutate)
    assert any("plain unit text" in e for e in errors), errors
    assert time.monotonic() - start < 5
