"""Deterministic fix options for a failed rule.

For a result that FAILED, find the smallest single-input changes that make THIS rule evaluate to PASS, using only the rule engine's own evaluation:

* a yes/no input: the other answer;
* a number with a unit: the boundary between the failing value and the nearest passing value, found by bisection (to 3 significant figures, on the passing side);
* a choice from a list: each other listed choice that passes.

Only inputs the engineer decides (declared `engineer_confirmed` in the rule) are changed; project facts, the system type and anything looked up are never offered.
Every option is a HYPOTHESIS to verify: the caller re-runs every dependent rule (`cross_rule`) and an option is accepted only if none breaks. Nothing here decides
compliance and nothing is invented: no text of a standard, no threshold, only the rule's own check evaluated on changed inputs.
"""
import dataclasses
import hashlib
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mep.engine import units
from mep.engine.loader import Rule
from mep.engine.model import InputValue, Outcome
from mep.engine.rule_eval import evaluate_rule

NEVER_CHANGED = frozenset({"system_type", "climate_zone", "building_class"})
FACTORS = (0.9, 1.1, 0.75, 1.25, 0.5, 1.5, 2.0, 0.25, 3.0, 5.0, 10.0, 0.1, 0.0)
BISECT_STEPS = 40


@dataclass(frozen=True)
class FixOption:
    rule_id: str
    input_name: str
    from_value: Any
    to_value: Any
    unit: str | None
    kind: str                                  # "yes_no" | "number" | "choice"

    @property
    def id(self) -> str:
        return hashlib.sha256(f"{self.rule_id}|{self.input_name}|{self.to_value!r}".encode()).hexdigest()[:16]

    @property
    def label(self) -> str:
        u = f" {self.unit}" if self.unit else ""
        return (f"Hypothesis: verify. Change {self.input_name} from {_show(self.from_value)}{u} to {_show(self.to_value)}{u}; "
                f"{self.rule_id} then evaluates to PASS.")


def _show(v: Any) -> str:
    return str(v) if not isinstance(v, float) else f"{v:.6g}"


def _passes(rule: Rule, values: Mapping[str, InputValue], name: str, new: InputValue) -> bool:
    return evaluate_rule(rule, {**values, name: new}).outcome == Outcome.PASS


def _round_sig(x: float, digits: int, up: bool) -> float:
    if x == 0 or not math.isfinite(x):
        return x
    scale = 10 ** (digits - 1 - math.floor(math.log10(abs(x))))
    return float((math.ceil(x * scale) if up else math.floor(x * scale)) / scale)


def _number_options(rule: Rule, values: Mapping[str, InputValue], name: str, given: InputValue) -> list[FixOption]:
    spec = rule.inputs[name]
    if not isinstance(given.value, int | float) or isinstance(given.value, bool) or not spec.unit or units.is_offset(given.unit or spec.unit):
        return []
    start = float(given.value)
    out: list[FixOption] = []
    for direction in (1, -1):
        found: float | None = None
        for f in FACTORS:
            cand = start * f if f != 0 else 0.0
            if (cand - start) * direction <= 0:
                continue
            if _passes(rule, values, name, dataclasses.replace(given, value=cand)):
                found = cand
                break
        if found is None:
            if start == 0 and direction == 1:      # a zero value has no multiple: try plain steps
                for step in (1.0, 2.0, 5.0, 10.0, 100.0):
                    if _passes(rule, values, name, dataclasses.replace(given, value=step)):
                        found = step
                        break
            if found is None:
                continue
        lo, hi = start, found                      # lo fails, hi passes: bisect to the boundary
        for _ in range(BISECT_STEPS):
            mid = (lo + hi) / 2
            if _passes(rule, values, name, dataclasses.replace(given, value=mid)):
                hi = mid
            else:
                lo = mid
        best = hi
        for digits in (3, 4, 6):                   # the nearest value that still passes, to as few figures as possible
            nearest = float(f"{best:.{digits - 1}e}")
            for r in (nearest, _round_sig(best, digits, up=(direction == 1))):
                if _passes(rule, values, name, dataclasses.replace(given, value=r)):
                    best = r
                    break
            else:
                continue
            break
        out.append(FixOption(rule.id, name, given.value, best, given.unit or spec.unit, "number"))
    out.sort(key=lambda o: abs(float(o.to_value) - start))
    return out[:1]                                 # the smaller of the two changes; the other direction would be a different design intent


def candidate_fixes(rule: Rule, values: Mapping[str, InputValue]) -> list[FixOption]:
    """Options that make `rule` PASS, nearest change first. Empty when the rule did not FAIL or no single change helps."""
    base = evaluate_rule(rule, values)
    if base.outcome != Outcome.FAIL:
        return []
    out: list[FixOption] = []
    for name in sorted(base.inputs_used):
        spec = rule.inputs.get(name)
        given = values.get(name)
        if spec is None or given is None or name in NEVER_CHANGED or spec.provenance != "engineer_confirmed":
            continue
        if spec.type == "bool" and isinstance(given.value, bool):
            if _passes(rule, values, name, dataclasses.replace(given, value=not given.value)):
                out.append(FixOption(rule.id, name, given.value, not given.value, None, "yes_no"))
        elif spec.type == "number":
            out += _number_options(rule, values, name, given)
        elif spec.type == "enum" and spec.values:
            for choice in spec.values:
                if choice != given.value and _passes(rule, values, name, dataclasses.replace(given, value=choice)):
                    out.append(FixOption(rule.id, name, given.value, choice, None, "choice"))
    return out
