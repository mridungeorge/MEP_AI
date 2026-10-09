"""Evaluate one rule against one set of inputs. Pure and deterministic; no clause or threshold lives here.

Order: applicability filters -> exempt_when -> needs_judgement_when -> check. NOT_APPLICABLE only when
applicability is definitively false (or an encoded exemption is true) on confirmed inputs; anything
the rule cannot decide, or any input problem, is NEEDS_JUDGEMENT (docs/engine-conventions.md).
"""
import ast
import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from mep.engine import units
from mep.engine.evaluator import FUNCS, EvalError, Evaluator, Missing, parse
from mep.engine.loader import InputSpec, Rule
from mep.engine.model import CONFIRMED, InputValue, Outcome, Provenance

STRUCTURAL = frozenset({"edition", "state", "exempt_when", "needs_judgement_when"})
MAX_FORMULA_DEPTH = 6


class ExtractedInputError(Exception):
    """An input with provenance 'extracted' reached the engine before gate 1 confirmed it."""

    def __init__(self, names: list[str]) -> None:
        super().__init__(f"extracted inputs not allowed: {', '.join(names)}")
        self.names = names


@dataclass
class Evaluation:
    outcome: Outcome
    causes: list[dict[str, str]] = field(default_factory=list)
    inputs_used: dict[str, dict[str, Any]] = field(default_factory=dict)
    near_miss: dict[str, Any] | None = None
    fix_hypotheses: list[str] = field(default_factory=list)


def _prepare(spec: InputSpec, given: InputValue) -> Any:
    """Type, domain and unit check one supplied input; return the value the evaluator will see."""
    try:
        provenance = Provenance(given.provenance)
    except ValueError as exc:
        raise Missing("provenance", f"{spec.name}: unknown provenance") from exc
    if provenance == Provenance.DEFAULT or provenance not in CONFIRMED:
        raise Missing("unconfirmed", spec.name)
    declared = spec.provenance
    if provenance != Provenance.ENGINEER_CONFIRMED and provenance.value != declared:
        raise Missing("provenance", f"{spec.name} must be {declared} or engineer_confirmed, not {provenance.value}")
    value = given.value
    if spec.type == "bool":
        if not isinstance(value, bool):
            raise Missing("type", f"{spec.name} must be true/false")
        return value
    if spec.type in ("enum", "string"):
        if not isinstance(value, str):
            raise Missing("type", f"{spec.name} must be text")
        if spec.values is not None and value not in spec.values:
            raise Missing("domain", f"{spec.name}={value!r} is not one of {list(spec.values)}")
        return value
    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise Missing("type", f"{spec.name} must be a whole number")
        return value
    if spec.unit:  # number with a unit
        try:
            quantity = units.coerce(value, spec.unit, given.unit)
            if quantity.magnitude < 0 and not units.is_offset_unit(quantity.units):
                raise Missing("range", f"{spec.name} cannot be negative")  # physical quantities are not < 0
            return quantity
        except units.UnitError as exc:
            raise Missing("unit", f"{spec.name}: {exc}") from exc
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Missing("type", f"{spec.name} must be a number")
    if value != value or value in (float("inf"), float("-inf")):  # noqa: PLR0124 - NaN check
        raise Missing("type", f"{spec.name} is not finite")
    return value


class _Context:
    """Lazily reads inputs (errors only matter when an expression actually reads them) and thresholds."""

    def __init__(self, rule: Rule, values: Mapping[str, InputValue]) -> None:
        self.rule = rule
        self.specs = rule.inputs
        self.values = values
        self.cache: dict[str, Any] = {}
        self.read_names: list[str] = []
        self._depth = 0
        self.evaluator = Evaluator(self.read, self.threshold)

    def read(self, name: str) -> Any:
        if name not in self.specs:
            raise EvalError(f"{name} is not a declared input of {self.rule.id}")
        if name not in self.read_names:
            self.read_names.append(name)
        if name not in self.cache:
            if name not in self.values:
                raise Missing("missing_input", name)
            self.cache[name] = _prepare(self.specs[name], self.values[name])
        return self.cache[name]

    def threshold(self, args: list[Any]) -> Any:
        name, keys = args[0], args[1:]
        table = self.rule.thresholds
        if name not in table:
            raise Missing("threshold", f"{name} is not defined")
        node: Any = table[name]
        unit: str | None = None
        for key in keys:
            if isinstance(node, dict) and "values" in node and "unit" in node:
                node, unit = node["values"], node["unit"]
            try:
                found = isinstance(node, dict) and key in node
            except TypeError as exc:  # unhashable key value
                raise Missing("threshold", f"unusable key under {name}") from exc
            if not found or isinstance(key, bool):
                raise Missing("threshold", f"no entry for {key!r} under {name}")
            node = node[key]
        if isinstance(node, dict) and "formula" in node:
            return self._formula(node)
        if isinstance(node, dict) and "value" in node:
            return self._leaf(node["value"], node.get("unit", unit))
        if isinstance(node, dict):
            raise Missing("threshold", f"incomplete path under {name}")
        return self._leaf(node, unit)

    @staticmethod
    def _leaf(value: Any, unit: str | None) -> Any:
        if value == "TODO_FROM_SOURCE" or unit is None:
            raise Missing("todo", "threshold value not yet typed from the official source")
        try:
            return units.threshold_quantity(value, unit)
        except units.UnitError as exc:
            raise Missing("unit", str(exc)) from exc

    def _formula(self, node: dict[str, Any]) -> Any:
        if self._depth >= MAX_FORMULA_DEPTH:
            raise Missing("threshold", "formula nesting too deep")
        self._depth += 1
        try:
            result = self.evaluator.evaluate(node["formula"])
        finally:
            self._depth -= 1
        if not units.valid_unit_text(node["unit"]):
            raise Missing("unit", "formula unit is not plain unit text")
        try:
            want = units.dimensionality(node["unit"])
        except units.UnitError as exc:
            raise Missing("unit", "formula unit is not a known unit") from exc
        if not hasattr(result, "dimensionality") or result.dimensionality != want:
            raise Missing("unit", f"formula does not produce {node['unit']}")
        return result


def _filter_ok(ctx: _Context, key: str, spec: Any) -> bool:
    value = ctx.read(key)
    if isinstance(spec, dict):
        if not set(spec) <= {"in", "not_in"}:
            raise Missing("rule", f"unsupported filter operator on {key}")
        return ("in" not in spec or value in spec["in"]) and ("not_in" not in spec or value not in spec["not_in"])
    if isinstance(spec, list):
        return bool(value in spec)
    return bool(value == spec)


def _truth(value: Any) -> bool:
    """An exemption/judgement expression must give true/false; anything else cannot be trusted."""
    if not isinstance(value, bool):
        raise Missing("type", "a condition did not give true/false")
    return value


def _cause(exc: Missing) -> dict[str, str]:
    return {"kind": exc.kind, "detail": exc.detail}


def _decide(ctx: _Context) -> tuple[Outcome, list[dict[str, str]]]:
    rule = ctx.rule
    aw = rule.applies_when
    causes: list[dict[str, str]] = []
    undecided = False
    for key, spec in aw.items():
        if key in STRUCTURAL:
            continue
        try:
            if not _filter_ok(ctx, key, spec):
                return Outcome.NOT_APPLICABLE, [{"kind": "filter", "detail": f"{key} does not match"}]
        except Missing as exc:
            undecided = True
            causes.append(_cause(exc))
    for expr in aw.get("exempt_when", []):
        try:
            if _truth(ctx.evaluator.evaluate(expr)):
                return Outcome.NOT_APPLICABLE, [{"kind": "exempt", "detail": expr}]
        except Missing as exc:
            undecided = True
            causes.append(_cause(exc))
    if undecided:
        return Outcome.NEEDS_JUDGEMENT, causes
    for expr in aw.get("needs_judgement_when", []):
        try:
            if _truth(ctx.evaluator.evaluate(expr)):
                return Outcome.NEEDS_JUDGEMENT, [{"kind": "judgement_condition", "detail": expr}]
        except Missing as exc:
            return Outcome.NEEDS_JUDGEMENT, [_cause(exc)]
    try:
        result = ctx.evaluator.evaluate(str(rule.raw["check"]))
    except Missing as exc:
        return Outcome.NEEDS_JUDGEMENT, [_cause(exc)]
    if not isinstance(result, bool):
        return Outcome.NEEDS_JUDGEMENT, [{"kind": "type", "detail": "check did not give true/false"}]
    outcomes = rule.raw["outcomes"]
    return Outcome(outcomes["true" if result else "false"]), []


def _near_miss(rule: Rule, values: Mapping[str, InputValue], base: Outcome, read: list[str]) -> dict[str, Any] | None:
    fraction = rule.raw.get("near_miss")
    if fraction is None or base not in (Outcome.PASS, Outcome.FAIL):
        return None
    flips: list[dict[str, Any]] = []
    skipped: list[str] = []
    for name in read:
        spec = rule.inputs[name]
        given = values.get(name)
        if spec.type != "number" or not spec.unit or given is None:
            continue
        if units.is_offset(given.unit or spec.unit):
            skipped.append(name)  # proportional change is meaningless for an absolute temperature
            continue
        for sign in (1, -1):
            changed = dataclasses.replace(given, value=given.value * (1 + sign * fraction))
            outcome, _ = _decide(_Context(rule, {**values, name: changed}))
            if outcome in (Outcome.PASS, Outcome.FAIL) and outcome != base:  # a judgement is not a flip
                flips.append({"input": name, "change": round(sign * fraction, 6), "becomes": outcome.value})
    return {"fraction": fraction, "is_near_miss": bool(flips), "flips": flips, "not_evaluated": skipped}


def check_rule_expressions(rule: Rule) -> list[str]:
    """Problems that would make the rule unevaluable: parse errors and names that are not declared inputs.

    Run before a run writes anything (e.g. a ledger override), so a mistyped rule is refused up front.
    """
    names = set(rule.inputs)
    problems: list[str] = []
    exprs = [str(rule.raw["check"])]
    aw = rule.applies_when
    exprs += [str(e) for key in ("exempt_when", "needs_judgement_when") for e in aw.get(key, [])]
    exprs += [str(n["formula"]) for n in rule.thresholds.values() if isinstance(n, dict) and "formula" in n]
    for expr in exprs:
        try:
            tree = parse(expr)
        except EvalError as exc:
            problems.append(f"{exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in names and node.id not in ("True", "False") \
                    and node.id not in FUNCS:
                problems.append(f"'{node.id}' is not a declared input")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "threshold":
                first = node.args[0]
                if isinstance(first, ast.Constant) and first.value not in rule.thresholds:
                    problems.append(f"threshold {first.value!r} is not defined")
    for key in aw:
        if key not in STRUCTURAL and key not in names:
            problems.append(f"applies_when key '{key}' is not a declared input")
    for u in declared_units(rule):
        try:
            units.unit_of(u)
        except units.UnitError as exc:
            problems.append(f"declared unit {u!r} is not usable: {exc}")
    return problems


def declared_units(rule: Rule) -> list[Any]:
    """Every unit string a rule declares: inputs, threshold leaves, groups and formulas."""
    found: list[Any] = [i.get("unit") for i in rule.raw["inputs"] if i.get("unit") is not None]

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "unit" in node:
                found.append(node["unit"])
            for child in node.values():
                walk(child)

    walk(rule.thresholds)
    return found


def evaluate_rule(rule: Rule, values: Mapping[str, InputValue]) -> Evaluation:
    """Evaluate `rule`. Raises ExtractedInputError if any supplied input is still 'extracted'."""
    extracted = sorted(n for n, v in values.items() if n in rule.inputs and v.provenance == Provenance.EXTRACTED)
    if extracted:
        raise ExtractedInputError(extracted)
    ctx = _Context(rule, values)
    outcome, causes = _decide(ctx)
    used: dict[str, dict[str, Any]] = {}
    for name in ctx.read_names:
        if name in values:
            given = values[name]
            used[name] = {"value": given.value, "unit": given.unit, "provenance": str(Provenance(given.provenance).value)}
    near = _near_miss(rule, values, outcome, ctx.read_names)
    fixes = [str(h) for h in rule.raw.get("fix_hypotheses", [])] if outcome == Outcome.FAIL else []
    return Evaluation(outcome, causes, used, near, fixes)
