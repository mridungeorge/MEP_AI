"""Regression tests for the fourth Sprint 1 adversarial review."""
import copy
import time
from datetime import date
from pathlib import Path

import pytest
import yaml
from mep.engine.jurisdiction import Override
from mep.engine.loader import RuleLoadError, load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import MAX_EVALUATIONS, RunRefused, run
from mep.engine.units import UnitError, coerce, valid_unit_text

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
DEAD25 = "NCC2025-J6D3-deadband"
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


# ---- the unit-text grammar --------------------------------------------------------------------

HANGERS = ["L/s*9^9_999_999", "L/s*9^9_999_999_999", "L/s*9^1_0000000000", "L/s*9^0_9999999999999",
           "L/s*99999^9_9999999", "9^9_999_999", "L/s^(9^9^9)", "L/s^9^9", "L/s*9_9", "L/s*1", "1", "L/s*9^0_0"]
GOOD = ["L/s", "kW", "W", "K", "degC", "degF", "m^2", "kg", "mm", "kW*h/m^2", "m^3/s", "m^2*K/W", "delta_degC",
        "kilowatt_hour", "dimensionless", "m^3/hour", "m^-1"]


@pytest.mark.parametrize("unit", HANGERS)
def test_digit_separators_and_numbers_in_unit_text_are_refused_fast(unit):
    start = time.monotonic()
    assert not valid_unit_text(unit)
    with pytest.raises(UnitError):
        coerce(1500, "L/s", unit)
    assert time.monotonic() - start < 2


@pytest.mark.parametrize("unit", GOOD)
def test_ordinary_units_are_accepted_by_the_grammar(unit):
    assert valid_unit_text(unit)


@pytest.mark.parametrize("unit", HANGERS)
def test_a_run_with_a_hanging_unit_text_gives_a_judgement_quickly(unit):
    start = time.monotonic()
    report = run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(1500, unit)))]), PACK)
    assert report["results"][0]["outcome"] == "NEEDS_JUDGEMENT"
    assert time.monotonic() - start < 5


# ---- declared units in rule data ---------------------------------------------------------------

def _pack_with(tmp_path, mutate):
    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True)
    (rules / "schema" / "rule.schema.json").write_text((ROOT / "rules/schema/rule.schema.json").read_text())
    raw = yaml.safe_load((ROOT / "rules/ncc2025/j6/NCC2025-J6D3-econ-cycle.yaml").read_text(encoding="utf-8"))
    mutate(raw)
    (rules / "ncc2025" / "j6").mkdir(parents=True)
    (rules / "ncc2025" / "j6" / "NCC2025-J6D3-econ-cycle.yaml").write_text(yaml.safe_dump(raw))
    return rules


@pytest.mark.parametrize("unit", ["W*9^9_999_999_999", "notaunit(", "L/s*1"])
def test_rule_files_with_unit_text_outside_the_grammar_do_not_load(tmp_path, unit):
    def mutate(raw):
        raw["inputs"][0]["unit"] = unit
    with pytest.raises(RuleLoadError):
        load_pack(_pack_with(tmp_path, mutate))


def test_an_unknown_formula_unit_is_a_rule_error_not_a_crash():
    broken = copy.deepcopy(PACK)
    rule = broken.rules[ECON25]
    rule.raw["threshold"]["values"]["f"] = {"formula": "threshold('by_climate_zone', climate_zone)", "unit": "notaunit"}
    rule.raw["check"] = "max_airside_component_airflow < threshold('f') or economy_cycle == True"
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs())]), broken)
    assert exc.value.code == "rule_error" and any("notaunit" in r for r in exc.value.reasons)


# ---- request bounds and exact types -----------------------------------------------------------

def test_duplicate_rule_ids_in_one_subject_are_refused():
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), [Subject("s", [ECON25, ECON25], econ_inputs())]), PACK)
    assert exc.value.code == "invalid_request"


def test_total_evaluations_are_capped():
    subjects = [Subject(f"s{i}", [ECON25, DEAD25] * 0 + [ECON25] + [f"X{j}" for j in range(99)], {})
                for i in range(MAX_EVALUATIONS // 100 + 2)]
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), subjects), PACK)
    assert exc.value.code == "invalid_request"


def test_long_texts_are_capped():
    for subject in (Subject("s" * 500, [], {}), Subject("s", [], {"economy_cycle": iv("x" * 600)}),
                    Subject("s", [], {"x": iv(1, "L" * 600)})):
        with pytest.raises(RunRefused):
            run(RunRequest(project(), [subject]), PACK)
    with pytest.raises(RunRefused):
        run(RunRequest(project(state="NT"), [Subject("s", [], {})],
                       Override("u", "approver", "reason " * 400)), PACK)


class _Hostile:
    def __eq__(self, other):
        raise RuntimeError("hostile __eq__")

    def __hash__(self):
        raise RuntimeError("hostile __hash__")

    def __str__(self):
        raise RuntimeError("hostile __str__")


class _HostileStr(str):
    __slots__ = ()

    def __eq__(self, other):
        raise RuntimeError("hostile")

    def __hash__(self):
        return 1


class _HostileInt(int):
    def __ge__(self, other):
        raise RuntimeError("hostile")

    __le__ = __lt__ = __gt__ = __ge__


class _HostileFloat(float):
    def __lt__(self, other):
        raise RuntimeError("hostile")

    __mul__ = __rmul__ = __gt__ = __lt__


class _FakeProv:
    value = "engineer_confirmed"

    def __eq__(self, other):
        raise RuntimeError("hostile")


@pytest.mark.parametrize("make", [
    lambda: RunRequest(project(ncc_edition=_Hostile()), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(climate_zone=_HostileInt(6)), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(facts_provenance=_FakeProv()), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(climate_zone_provenance=_FakeProv()), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(state=_HostileStr("VIC")), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(building_class=_HostileStr("5")), [Subject("s", [ECON25], econ_inputs())]),
    lambda: RunRequest(project(), [Subject("s", [ECON25], econ_inputs(system_type=iv(_HostileStr("x"))))]),
    lambda: RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(_HostileFloat(1500.0), "L/s")))]),
    lambda: RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=InputValue(1500, "L/s", _FakeProv())))]),   # type: ignore[arg-type]
    lambda: RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(1500, _HostileStr("L/s"))))]),
    lambda: RunRequest(project(), [Subject(_HostileStr("s"), [ECON25], econ_inputs())]),
    lambda: RunRequest(project(), [Subject("s", [_HostileStr(ECON25)], econ_inputs())]),
])
def test_hostile_field_types_are_refused_not_crashed(make):
    with pytest.raises(RunRefused) as exc:
        run(make(), PACK)
    assert exc.value.code == "invalid_request"


def test_the_request_is_snapshotted_so_later_mutation_cannot_matter():
    class Mutating(Subject):
        pass

    req = RunRequest(project(), [Subject("s", [ECON25], econ_inputs())])
    report = run(req, PACK)
    req.subjects[0].inputs["max_airside_component_airflow"] = iv(1, "L/s")
    assert report["results"][0]["outcome"] == "FAIL"      # the report reflects the request as validated
