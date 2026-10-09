"""Unit handling for rule inputs and thresholds. Every number passes through pint here.

A unit problem is never fixed up silently: it raises UnitError, which the rule evaluator turns into
NEEDS_JUDGEMENT. Absolute temperatures (degC/degF, units with an offset zero) never convert to or
from differences (K, delta_degC) or to each other.

Unit text never reaches pint's string parser. pint's preprocessor rewrites words such as ``sq``, ``cubic``,
``squared`` and `` per `` into real powers and reads ``9_999_999`` as a number, so text it parses can hang the
process (``sq square cubic m^9`` becomes ``m**2**2**3**9``). Instead the text is checked against a strict
grammar (no whitespace, no standalone numbers, one single-digit exponent per name, ``*`` and ``/`` only) and
the unit is built from pint objects one name at a time. Conversion factors are bounded.
"""
import functools
import math
import re
from typing import Any

import pint

UREG: Any = pint.UnitRegistry()

_NAME = r"[A-Za-z][A-Za-z0-9_]{0,31}"
_TERM = rf"{_NAME}(?:\^-?[0-9])?"
_UNIT_TEXT = re.compile(rf"^{_TERM}(?:[*/]{_TERM}){{0,15}}$")
_PIECE = re.compile(rf"(?P<op>[*/])?(?P<name>{_NAME})(?:\^(?P<exp>-?[0-9]))?")
MAX_UNIT_CHARS = 96
FACTOR_MIN = 1e-12
FACTOR_MAX = 1e12
CACHE_LIMIT = 20000


class UnitError(ValueError):
    """A value or unit that cannot be used as declared."""


def valid_unit_text(unit: Any) -> bool:
    """True only for unit text in the strict grammar."""
    return type(unit) is str and len(unit) <= MAX_UNIT_CHARS and _UNIT_TEXT.fullmatch(unit) is not None


def _non_multiplicative(unit: Any) -> bool:
    """True if the unit cannot be scaled by a plain factor (degC, degF and logarithmic units like dB)."""
    try:
        UREG.Quantity(1, unit) * 2
    except pint.errors.OffsetUnitCalculusError:
        return True
    except Exception:  # noqa: BLE001 - anything else is a different problem, not "non-multiplicative"
        return False
    return False


def is_offset_unit(unit: Any) -> bool:
    """True for a pint TEMPERATURE unit with an offset zero (degC, degF): an absolute temperature."""
    return _non_multiplicative(unit) and unit.dimensionality == UREG.Unit("kelvin").dimensionality


def is_log_unit(unit: Any) -> bool:
    """True for logarithmic units (dB, neper, octave ...): non-multiplicative but not a temperature."""
    return _non_multiplicative(unit) and unit.dimensionality != UREG.Unit("kelvin").dimensionality


def _trim_pint_caches() -> None:
    """pint caches every distinct unit it sees; keep that bounded in a long-running process."""
    cache = getattr(UREG, "_cache", None)
    for name in ("parse_unit", "dimensionality", "root_units", "conversion_factor", "dimensional_equivalents"):
        table = getattr(cache, name, None)
        if isinstance(table, dict) and len(table) > CACHE_LIMIT:
            table.clear()


def _is_dimensionless(unit: Any) -> bool:
    try:
        return bool(unit.dimensionless)
    except Exception:  # noqa: BLE001
        return False


@functools.lru_cache(maxsize=4096)
def _build(text: str) -> Any:
    if not valid_unit_text(text):
        raise UnitError("unit text is not a plain unit expression")
    unit: Any = None
    terms = 0
    offset_seen = False
    for piece in _PIECE.finditer(text):
        terms += 1
        try:
            term = UREG.Unit(piece["name"])
        except Exception as exc:  # unknown name
            raise UnitError(f"unknown unit {piece['name']!r}") from exc
        if is_log_unit(term):
            raise UnitError("logarithmic units are not supported")
        if _is_dimensionless(term) and (piece["exp"] or len(text) != len(piece["name"])):
            # percent, ppm, pi, turn, radian ... inside a compound unit would silently rescale the value
            raise UnitError("a dimensionless unit cannot be part of a compound unit")
        offset_seen = offset_seen or is_offset_unit(term)
        if piece["exp"]:
            term = term ** int(piece["exp"])
        if unit is None:
            unit = term
        elif piece["op"] == "*":
            unit = unit * term
        else:
            unit = unit / term
    if offset_seen and (terms > 1 or "^" in text):
        raise UnitError("an absolute temperature cannot be part of a compound unit")
    if not offset_seen:
        try:
            factor = float(UREG.Quantity(1, unit).to_base_units().magnitude)
        except Exception as exc:
            raise UnitError("unit has no usable base conversion") from exc
        if not math.isfinite(factor) or not FACTOR_MIN <= abs(factor) <= FACTOR_MAX:
            raise UnitError("unit conversion factor is outside the supported range")
    _trim_pint_caches()
    return unit


def unit_of(text: str) -> Any:
    """The pint unit for plain unit text (raises UnitError otherwise)."""
    if type(text) is not str:
        raise UnitError("unit must be text")
    return _build(text)


def dimensionality(text: str) -> Any:
    return unit_of(text).dimensionality


def is_offset(text: str) -> bool:
    """is_offset_unit for unit text; text that is not plain unit text is simply not an offset unit."""
    try:
        return is_offset_unit(unit_of(text))
    except UnitError:
        return False


def coerce(value: Any, declared_unit: str, supplied_unit: str | None) -> Any:
    """Return a pint Quantity for `value`, checked against the unit the rule declares.

    The supplied unit is mandatory: a missing or empty unit is a UnitError, never a cast to the declared unit.
    Raises UnitError for non-numbers (bool, NaN, inf, text), unknown or non-plain units, a different dimension,
    a conversion factor outside the supported range, a converted value that overflows, or any mix of offset and
    non-offset (or two different offset) temperature units.
    """
    if not isinstance(supplied_unit, str) or not supplied_unit.strip():
        raise UnitError("no unit was supplied with the value")
    try:
        finite = (not isinstance(value, bool)) and isinstance(value, int | float) and math.isfinite(value)
    except OverflowError:  # an int too large for a float
        finite = False
    if not finite:
        raise UnitError("not a finite number")
    unit = unit_of(supplied_unit)
    declared = unit_of(declared_unit)
    try:
        quantity = UREG.Quantity(value, unit)
        same_dimension = quantity.dimensionality == declared.dimensionality
    except Exception as exc:
        raise UnitError(f"unusable unit {supplied_unit!r}: {exc}") from exc
    if not same_dimension:
        raise UnitError(f"{supplied_unit} is not a unit of {declared_unit}")
    if is_offset_unit(unit):
        try:
            kelvin = float(quantity.to("kelvin").magnitude)
        except Exception as exc:
            raise UnitError("temperature cannot be converted to kelvin") from exc
        if kelvin < 0:
            raise UnitError("temperature is below absolute zero")
    if supplied_unit != declared_unit:
        if is_offset_unit(unit) or is_offset_unit(declared):
            raise UnitError(f"{supplied_unit} and {declared_unit} are not interchangeable temperature units")
        try:
            converted = float(quantity.to(declared).magnitude)
        except Exception as exc:
            raise UnitError("value cannot be converted to the declared unit") from exc
        if not math.isfinite(converted):
            raise UnitError("value overflows when converted to the declared unit")
    return quantity


def threshold_quantity(value: Any, unit: str) -> Any:
    """A threshold cell as a Quantity (the unit is the cell's own)."""
    return coerce(value, unit, unit)
