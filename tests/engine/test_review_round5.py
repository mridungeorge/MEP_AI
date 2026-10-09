"""Regression tests for the fifth Sprint 1 adversarial review (pint preprocessor, overflow, TOCTOU, sizes)."""
import json
import time
from datetime import date
from pathlib import Path

import pytest
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.report import to_json
from mep.engine.runner import RunRefused, run
from mep.engine.units import UnitError, coerce, unit_of, valid_unit_text

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
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


# ---- pint's string preprocessor words must never be reachable ---------------------------------

TOWERS = ["sq square cubic m^9", "sq square cubic m^4", "sq square cubic L^4/s", "sq square cubic L cubed/s",
          "m squared cubed", "m per s", "L per s", "square m", "cubic m", "sq m", "m cubed", "L/s per s",
          "sq\tsquare m^9", "m\nsquared"]


@pytest.mark.parametrize("unit", TOWERS)
def test_pint_preprocessor_words_and_whitespace_are_refused_fast(unit):
    start = time.monotonic()
    assert not valid_unit_text(unit)
    with pytest.raises(UnitError):
        unit_of(unit)
    with pytest.raises(UnitError):
        coerce(1500, "L/s", unit)
    assert time.monotonic() - start < 1


@pytest.mark.parametrize("unit", TOWERS)
def test_a_run_with_a_power_tower_unit_gives_a_judgement_quickly(unit):
    start = time.monotonic()
    r = run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(1500, unit)))]), PACK)["results"][0]
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"
    assert time.monotonic() - start < 3


def test_whitespace_multiplication_is_not_a_unit_expression():
    for unit in ("W/m^2 K", "m^2 K/W", "kW h"):
        assert not valid_unit_text(unit)
    assert valid_unit_text("W/m^2*K") and valid_unit_text("kW*h/m^2")


# ---- prefix stacking cannot overflow into a wrong result -------------------------------------

@pytest.mark.parametrize("unit", ["Ym^9/ym^7*K/W", "Ym^9/ym^6/s", "ym^9*Ym^9", "YYm"])
def test_absurd_conversion_factors_are_refused(unit):
    with pytest.raises(UnitError):
        coerce(1.0, "m^2*K/W" if "K/W" in unit else "m^3/s", unit)


def test_a_prefix_overflow_cannot_produce_a_wrong_pass():
    pack = PACK
    rid = "NCC2025-J6D6-duct-insulation"
    assert rid in pack.rules
    inputs = {"duct_type": iv("cushion_box"), "duct_location": iv("other"),
              "duct_insulation_r_value": iv(1e-300, "Ym^9/ym^7*K/W"),
              "connecting_duct_insulation_r_value": iv(1e300, "m^2*K/W")}
    for name in ("duct_in_only_or_last_room_served", "duct_is_conditioned_space_interface_fitting",
                 "duct_is_return_air_in_conditioned_space", "duct_is_outdoor_or_exhaust_air",
                 "duct_is_in_situ_ahu_floor", "duct_is_flexible_fan_connection", "duct_is_active_component",
                 "is_electricity_substation"):
        inputs[name] = iv(False)
    inputs["system_type"] = iv("air_conditioning")
    r = run(RunRequest(project(), [Subject("s", [rid], inputs)]), pack)["results"][0]
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"


def test_ordinary_units_still_convert_correctly():
    assert coerce(1.5, "L/s", "m^3/s").to("L/s").magnitude == pytest.approx(1500)
    assert coerce(2, "kW", "W").to("kW").magnitude == pytest.approx(0.002)
    assert coerce(1, "h", "h").magnitude == 1
    assert coerce(20, "degC", "degC").magnitude == 20


# ---- the request is read once ------------------------------------------------------------------

def test_a_subject_whose_inputs_change_between_reads_cannot_smuggle_values_past_validation():
    class Shifty(Subject):
        reads = 0

        @property  # type: ignore[override]
        def inputs(self):
            Shifty.reads += 1
            if Shifty.reads == 1:
                return econ_inputs()
            return econ_inputs(max_airside_component_airflow=iv(1500, "sq square cubic L^2"))

        @inputs.setter
        def inputs(self, value):
            pass

    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), [Shifty("s", [ECON25], {})]), PACK)
    assert exc.value.code == "invalid_request"          # subclasses are not accepted at all


def test_a_project_whose_fields_change_between_reads_cannot_choose_a_different_class():
    class Shifty(ProjectFacts):
        reads = 0

        @property  # type: ignore[override]
        def building_class(self):
            Shifty.reads += 1
            return "5" if Shifty.reads == 1 else "2"

        @building_class.setter
        def building_class(self, value):
            pass

    p = Shifty("TAS", "NCC2022", 6, "5", D)
    with pytest.raises(RunRefused):
        run(RunRequest(p, [Subject("s", [], {})]), PACK)


def test_a_request_object_that_is_not_a_runrequest_is_refused():
    class Fake:
        def __init__(self):
            self.project = project()
            self.subjects = []

    with pytest.raises(RunRefused) as exc:
        run(Fake(), PACK)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_request"


# ---- sizes ---------------------------------------------------------------------------------------

def test_huge_whole_numbers_are_refused_and_reports_stay_serialisable():
    for big in (10**5000, 10**16, -(10**16)):
        with pytest.raises(RunRefused):
            run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(economy_cycle=iv(big)))]), PACK)
    report = run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs())]), PACK)
    assert json.loads(to_json(report))["summary"]["FAIL"] == 1


def test_pint_caches_stay_bounded():
    from mep.engine import units
    for i in range(200):
        try:
            unit_of(f"m^{i % 10}*kg^{(i // 10) % 10}*s^-{(i // 100) % 10}")
        except UnitError:
            pass
    cache = getattr(units.UREG, "_cache", None)
    for name in ("parse_unit", "dimensionality", "root_units"):
        table = getattr(cache, name, None)
        if isinstance(table, dict):
            assert len(table) <= units.CACHE_LIMIT + 1000
