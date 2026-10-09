"""Adapter that runs a rule dict's own test cases on the real engine (mep.engine).

Sprint 0's standalone reference evaluator was retired in Sprint 1: this only converts the plain
values used in rule YAML tests (a number, or {value, unit}) into engine InputValues.
"""
from pathlib import Path
from typing import Any

from mep.engine.evaluator import EvalError, Missing
from mep.engine.loader import Rule as EngineRule
from mep.engine.model import InputValue
from mep.engine.rule_eval import _Context, evaluate_rule

NA, NJ = "NOT_APPLICABLE", "NEEDS_JUDGEMENT"
__all__ = ["NA", "NJ", "EvalError", "Missing", "Rule"]


def to_inputs(supplied: dict[str, Any], rule: dict[str, Any]) -> dict[str, InputValue]:
    """Rule YAML tests give bare numbers; the test adapter (not the engine) states the declared unit.

    A test value written as {value, unit} is passed exactly as given, so unit-mismatch tests still bite.
    """
    declared = {i["name"]: i.get("unit") for i in rule["inputs"]}
    out = {}
    for name, raw in supplied.items():
        if isinstance(raw, dict) and "value" in raw:
            out[name] = InputValue(raw["value"], raw.get("unit"))
        elif declared.get(name) and isinstance(raw, int | float) and not isinstance(raw, bool):
            out[name] = InputValue(raw, declared[name])
        else:
            out[name] = InputValue(raw)
    return out


class Rule:
    def __init__(self, rule: dict[str, Any]):
        self.r = rule
        self.engine_rule = EngineRule(rule, Path("."), "")

    def run(self, supplied: dict[str, Any]) -> str:
        return evaluate_rule(self.engine_rule, to_inputs(supplied, self.r)).outcome.value

    def evaluate(self, expr: str, supplied: dict[str, Any]) -> Any:
        """Evaluate one expression the way the engine does (raises Missing when it cannot)."""
        return _Context(self.engine_rule, to_inputs(supplied, self.r)).evaluator.evaluate(expr)
