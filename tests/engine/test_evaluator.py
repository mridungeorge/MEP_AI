import pytest
from mep.engine.evaluator import EvalError, Evaluator, Missing
from mep.engine.units import coerce


def make(values=None, thresholds=None):
    values = values or {}
    thresholds = thresholds or {}
    reads: list[str] = []

    def read(name):
        reads.append(name)
        if name not in values:
            raise Missing("missing_input", name)
        return values[name]

    def threshold(args):
        key = tuple(args)
        if key not in thresholds:
            raise Missing("threshold", str(key))
        return thresholds[key]

    ev = Evaluator(read, threshold)
    ev.reads = reads
    return ev


@pytest.mark.parametrize("expr", [
    "x.real", "x[0]", "(lambda: 1)()", "[a for a in x]", "x ** 2", "f'{x}'", "(y := 1)", "open('f')",
    "__import__('os')", "x if x else x", "*x", "x @ x", "x // 2", "x % 2", "x << 1", "~x", "{1: 2}", "{1}",
    "await x", "yield", "x; y", "import os", "print(x)", "abs(x, key=1)", "threshold(x=1)", "getattr(x, 'a')",
    "eval('1')", "exec('1')", "type(x)", "min", "x.__class__",
])
def test_everything_off_the_whitelist_is_rejected(expr):
    with pytest.raises(EvalError):
        make({"x": 1}).evaluate(expr)


def test_whitelisted_syntax_evaluates():
    ev = make({"a": 3, "b": 4, "flag": True}, {("limit",): 10})
    assert ev.evaluate("a < b and flag == True") is True
    assert ev.evaluate("not (a > b) or False") is True
    assert ev.evaluate("min(a, b) + max(a, b) - abs(-1) == 6") is True
    assert ev.evaluate("(a + b) * 2 / 2 <= threshold('limit')") is True
    assert ev.evaluate("a in [1, 2, 3]") is True
    assert ev.evaluate("'x' not in ['y']") is True


def test_and_or_short_circuit_so_unread_inputs_are_not_needed():
    ev = make({"a": False})
    assert ev.evaluate("a and missing_input") is False
    ev = make({"a": True})
    assert ev.evaluate("a or missing_input") is True
    assert ev.reads == ["a"]


def test_missing_input_is_a_judgement_not_an_error():
    with pytest.raises(Missing) as exc:
        make().evaluate("nope > 1")
    assert exc.value.kind == "missing_input"


def test_division_by_zero_is_a_judgement():
    with pytest.raises(Missing) as exc:
        make({"a": 1, "b": 0}).evaluate("a / b > 1")
    assert exc.value.kind == "arithmetic"


def test_mixing_text_and_numbers_is_a_judgement():
    with pytest.raises(Missing) as exc:
        make({"a": "5"}).evaluate("a > 1")
    assert exc.value.kind == "type"


def test_quantities_compare_through_pint():
    ev = make({"p": coerce(900, "W", "W"), "q": coerce(1, "kW", "kW")})
    assert ev.evaluate("p < q") is True


def test_offset_temperature_arithmetic_is_refused_not_silently_wrong():
    ev = make({"t1": coerce(40, "degC", "degC"), "t2": coerce(20, "degC", "degC")})
    with pytest.raises(Missing) as exc:
        ev.evaluate("t1 - t2 > 5")
    assert exc.value.kind in ("unit", "arithmetic")


def test_size_limits_stop_pathological_expressions():
    with pytest.raises(EvalError):
        make().evaluate("1 + " * 3000 + "1")
    with pytest.raises(EvalError):
        make().evaluate("-(" * 60 + "1" + ")" * 60)
    with pytest.raises(EvalError):
        make().evaluate("")


def test_threshold_requires_a_literal_name_and_known_key():
    with pytest.raises(EvalError):
        make({"x": "a"}).evaluate("threshold(x)")
    with pytest.raises(Missing):
        make().evaluate("threshold('nope') > 1")


def test_a_quantity_never_equals_or_matches_a_bare_number_silently():
    ev = make({"t": coerce(20, "degC", "degC"), "q": coerce(3, "L/s", "L/s"), "n": 3})
    for expr in ("t != 5", "q == 3", "q in [3]", "t == n"):
        with pytest.raises(Missing) as exc:
            ev.evaluate(expr)
        assert exc.value.kind == "unit"


def test_absolute_temperatures_in_different_units_are_not_compared():
    ev = make({"c": coerce(20, "degC", "degC"), "f": coerce(68, "degF", "degF")})
    with pytest.raises(Missing):
        ev.evaluate("c == f")
