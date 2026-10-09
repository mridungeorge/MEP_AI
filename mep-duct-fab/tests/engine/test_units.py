
import pytest
from mep.engine.units import UnitError, coerce, is_offset


def test_same_dimension_units_convert_through_pint():
    q = coerce(5, "kW", "W")
    assert q.to("kW").magnitude == pytest.approx(0.005)
    assert coerce(9000, "L/s", "L/s").to("L/s").magnitude == 9000


def test_wrong_dimension_is_a_unit_error_never_a_cast():
    with pytest.raises(UnitError):
        coerce(5, "kW", "kg")
    with pytest.raises(UnitError):
        coerce(5, "L/s", "m")


def test_unparseable_unit_is_a_unit_error():
    with pytest.raises(UnitError):
        coerce(5, "kW", "furlongs_per_fortnight_squared_x")


@pytest.mark.parametrize("bad", [True, False, float("nan"), float("inf"), "5", None, [1]])
def test_non_numbers_are_rejected(bad):
    with pytest.raises(UnitError):
        coerce(bad, "kW", "kW")


def test_offset_units_are_absolute_temperatures():
    assert is_offset("degC") and is_offset("degF")
    assert not is_offset("K") and not is_offset("delta_degC") and not is_offset("mm")


@pytest.mark.parametrize(("declared", "supplied"), [
    ("K", "degC"), ("degC", "K"), ("degC", "degF"), ("degC", "delta_degC"), ("K", "degF"),
])
def test_absolute_and_difference_temperatures_never_convert_silently(declared, supplied):
    with pytest.raises(UnitError):
        coerce(2, declared, supplied)


def test_matching_temperature_units_pass():
    assert coerce(20, "degC", "degC").magnitude == 20
    assert coerce(2, "K", "delta_degC").to("K").magnitude == pytest.approx(2)
    assert coerce(2, "K", "kelvin").magnitude == 2


@pytest.mark.parametrize("missing", [None, "", "   "])
def test_a_missing_unit_is_an_error_never_a_cast_to_the_declared_unit(missing):
    with pytest.raises(UnitError):
        coerce(3000, "L/s", missing)


def test_huge_numbers_are_a_unit_error_not_a_crash():
    with pytest.raises(UnitError):
        coerce(10**400, "L/s", "L/s")
