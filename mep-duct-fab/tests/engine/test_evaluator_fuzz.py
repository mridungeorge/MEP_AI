"""Hypothesis fuzz: nothing outside the whitelist can execute, and nothing crashes the evaluator."""
import builtins
import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from mep.engine.evaluator import EvalError, Evaluator, Missing

CANARY = Path(__file__).parent / "canary_must_not_exist.txt"
ESCAPES = [
    "__import__('os').system('echo pwn')",
    "open('canary_must_not_exist.txt', 'w')",
    "().__class__.__bases__[0].__subclasses__()",
    "[c for c in ().__class__.__mro__]",
    "(lambda: __import__('os'))()",
    "getattr(x, '__class__')",
    "x.__globals__",
    "eval('1+1')", "exec('import os')", "compile('1', 'a', 'eval')",
    "globals()", "locals()", "vars()", "dir()", "breakpoint()", "input()",
    "x if True else x", "f'{x.__class__}'", "[1,2][0]", "{**{}}", "print(x)",
]

NAMES = ["x", "y", "a_b", "threshold", "min", "max", "abs", "True", "False", "None", "__import__", "eval", "open"]
TOKENS = NAMES + ["1", "2.5", "'s'", '"t"', "(", ")", "[", "]", ",", ".", "+", "-", "*", "/", "<", ">", "<=", ">=", "==",
                  "!=", "and", "or", "not", "in", "**", "//", "%", ":=", "lambda", "for", "if", "else", "yield", ";",
                  "\n", "@", "~", "{", "}", "`", "\\", "__class__", "0x10", "1e999", "\x00", "é"]


def evaluator():
    def read(name):
        if name in ("x", "y"):
            return 1
        raise Missing("missing_input", name)

    def threshold(args):
        raise Missing("threshold", str(args))

    return Evaluator(read, threshold)


def run_safely(expr):
    try:
        return evaluator().evaluate(expr)
    except (EvalError, Missing):
        return None


def test_known_escape_payloads_are_rejected_and_have_no_effect():
    before = set(sys.modules)
    for payload in ESCAPES:
        try:
            evaluator().evaluate(payload)
        except (EvalError, Missing):
            continue
        raise AssertionError(f"payload was accepted: {payload}")
    assert not CANARY.exists()
    assert set(sys.modules) - before <= {"encodings.idna"}


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.text(max_size=300))
def test_arbitrary_text_never_crashes_or_escapes(text):
    try:
        evaluator().evaluate(text)
    except (EvalError, Missing):
        pass
    assert not CANARY.exists()


@settings(max_examples=3000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(TOKENS), min_size=1, max_size=40).map(" ".join))
def test_token_soup_never_crashes_or_escapes(expr):
    try:
        evaluator().evaluate(expr)
    except (EvalError, Missing):
        pass
    assert not CANARY.exists()


def test_evaluation_cannot_reach_builtins(monkeypatch):
    """Even if a payload were somehow evaluated, builtins that matter are poisoned for the call."""
    def boom(*_a, **_k):
        raise AssertionError("builtin was called")

    for name in ("open", "eval", "exec"):
        monkeypatch.setattr(builtins, name, boom)
    for payload in ESCAPES:
        run_safely(payload)


num = st.one_of(st.integers(-10**6, 10**6), st.floats(-1e6, 1e6, allow_nan=False, allow_infinity=False))


@st.composite
def whitelisted(draw, depth=0):
    if depth > 3 or draw(st.booleans()):
        return draw(st.sampled_from(["x", "y", "True", "False", "1", "2.5", "(x + y)", "'s'"]))
    a, b = draw(whitelisted(depth + 1)), draw(whitelisted(depth + 1))
    op = draw(st.sampled_from(["+", "-", "*", "/", "<", ">", "<=", ">=", "==", "!=", "and", "or"]))
    return f"({a} {op} {b})"


@settings(max_examples=1000, deadline=None)
@given(whitelisted())
def test_whitelisted_expressions_only_ever_return_or_raise_missing(expr):
    try:
        evaluator().evaluate(expr)
    except Missing:
        pass
