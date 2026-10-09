"""Regression tests for the sixth Sprint 1 adversarial review."""
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from mep.engine import units
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run
from mep.engine.units import UnitError, coerce, is_offset_unit, unit_of

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"
D = date(2026, 10, 6)


def iv(v, u=None, p=Provenance.ENGINEER_CONFIRMED):
    return InputValue(v, u, p)


def project():
    return ProjectFacts("VIC", "NCC2025", 6, "5", D)


def econ_inputs(**over):
    base = {"max_airside_component_airflow": iv(1500, "L/s"), "economy_cycle": iv(False),
            "system_type": iv("air_conditioning"), "provides_required_mech_ventilation": iv(True),
            "dehumidification_control_needed": iv(False), "is_electricity_substation": iv(False)}
    base.update(over)
    return base


@pytest.mark.parametrize("unit", ["L/s*percent", "L/s*ppm", "L/s*pi", "L/s*turn", "L/s*radian", "L/s*degree",
                                  "L/s*count", "L/s*permille", "L/s*arcminute", "m^2*percent", "percent^2"])
def test_dimensionless_names_cannot_rescale_a_value_inside_a_compound_unit(unit):
    with pytest.raises(UnitError):
        unit_of(unit)
    r = run(RunRequest(project(), [Subject("s", [ECON25], econ_inputs(
        max_airside_component_airflow=iv(150000, unit)))]), PACK)["results"][0]
    assert r["outcome"] == "NEEDS_JUDGEMENT" and r["causes"][0]["kind"] == "unit"


def test_a_lone_dimensionless_unit_is_still_a_unit():
    assert unit_of("dimensionless").dimensionless
    assert coerce(5, "dimensionless", "dimensionless").magnitude == 5


@pytest.mark.parametrize("value", [-300, -273.16, -1000])
def test_temperatures_below_absolute_zero_are_refused(value):
    with pytest.raises(UnitError):
        coerce(value, "degC", "degC")
    with pytest.raises(UnitError):
        coerce(-500, "degF", "degF")


def test_real_cold_temperatures_are_fine():
    assert coerce(-5, "degC", "degC").magnitude == -5
    assert coerce(-273.15, "degC", "degC").magnitude == pytest.approx(-273.15)


@pytest.mark.parametrize("unit", ["dB", "decibel", "neper", "Np", "octave", "decade", "dBm", "dBW", "dBu"])
def test_logarithmic_units_are_not_supported(unit):
    with pytest.raises(UnitError):
        unit_of(unit)
    assert not is_offset_unit(units.UREG.Unit("kelvin"))


def test_offset_means_temperature_only():
    assert is_offset_unit(units.UREG.Unit("degC")) and is_offset_unit(units.UREG.Unit("degF"))
    assert not is_offset_unit(units.UREG.Unit("decibel"))


def test_pint_caches_really_are_bounded(monkeypatch):
    """Deleting the trim must make this fail: shrink the limit and push many distinct units through."""
    monkeypatch.setattr(units, "CACHE_LIMIT", 300)
    units._build.cache_clear()
    for i in range(1500):
        try:
            coerce(1.0, "L/s", f"L^{i % 10}*kg^{(i // 10) % 10}*s^-{(i // 100) % 10}*m^{(i // 1000) % 10}/s")
        except UnitError:
            pass
        for j in range(3):
            try:
                unit_of(f"m^{i % 10}*mol^{j}*A^{(i // 10) % 10}*K^{(i // 100) % 10}*cd^{(i // 7) % 10}")
            except UnitError:
                pass
    cache = units.UREG._cache
    for name in ("parse_unit", "dimensionality", "root_units", "conversion_factor", "dimensional_equivalents"):
        table = getattr(cache, name, None)
        if isinstance(table, dict):
            assert len(table) <= 300 + 400, (name, len(table))      # a trim happened; growth is bounded


def test_total_inputs_per_run_are_capped():
    from mep.engine.runner import MAX_TOTAL_INPUTS
    per = 500
    n = MAX_TOTAL_INPUTS // per + 2
    subjects = [Subject(f"s{i}", [], {f"x{j}": iv(1.0, "m") for j in range(per)}) for i in range(n)]
    with pytest.raises(RunRefused) as exc:
        run(RunRequest(project(), subjects), PACK)
    assert exc.value.code == "invalid_request"


# ---- scripts/threshold_diff.py never hands text to pint's string parser --------------------------

def test_threshold_diff_canon_is_safe_on_hostile_unit_text():
    sys.path.insert(0, str(ROOT / "scripts"))
    import threshold_diff as td

    for text in ("m**9**9**9", "sq square cubic m**4", "L/s*9^9_999_999", "sq square cubic L cubed/s"):
        start = time.monotonic()
        td.canon(1.0, text)
        assert time.monotonic() - start < 2
    assert td.canon(1000, "W")[0] == td.canon(1, "kW")[0]
