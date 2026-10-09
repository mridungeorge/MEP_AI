"""AST-whitelist expression evaluator for rule `check` / `exempt_when` / `needs_judgement_when`.

Only these are accepted: names (resolved by the caller), numeric/text/boolean constants,
comparisons (incl. in / not in a literal list), and/or/not, + - * / and unary minus, literal lists
and the calls threshold(), min(), max(), abs(). Anything else raises EvalError at parse time.
The expression is never compiled or executed: the tree is walked here.

A value that cannot be used (missing input, wrong unit, wrong type, division by zero, absolute
temperature arithmetic) raises Missing, which callers map to NEEDS_JUDGEMENT.
"""
import ast
import functools
import math
import operator
from collections.abc import Callable
from typing import Any

from mep.engine import units

MAX_LEN = 8000
MAX_NODES = 2500
MAX_DEPTH = 40
MAX_STEPS = 60000
FUNCS = ("threshold", "min", "max", "abs")
ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.BinOp, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Name, ast.Load, ast.Constant, ast.Call, ast.List, ast.Tuple,
)
_CMP: dict[type, Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
    ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_ARITH: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
}


class EvalError(ValueError):
    """The expression is not in the whitelist (a rule authoring error, not a data problem)."""


class Missing(Exception):
    """A value could not be used. `kind` names the cause; `detail` says which input/threshold."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _depth(node: ast.AST, level: int = 0) -> int:
    if level > MAX_DEPTH:
        raise EvalError("expression nested too deeply")
    return max([level, *(_depth(child, level + 1) for child in ast.iter_child_nodes(node))])


@functools.lru_cache(maxsize=2048)
def parse(expr: str) -> ast.Expression:
    text = expr.strip()
    if not text or len(text) > MAX_LEN:
        raise EvalError("expression is empty or too long")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        raise EvalError(f"does not parse: {exc}") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > MAX_NODES:
        raise EvalError("expression has too many nodes")
    _depth(tree)
    callee_names = {id(n.func) for n in nodes if isinstance(n, ast.Call)}
    for node in nodes:
        if not isinstance(node, ALLOWED_NODES):
            raise EvalError(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Constant) and not _allowed_constant(node.value):
            raise EvalError("constant not allowed")
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise EvalError("dunder names are not allowed")
        if isinstance(node, ast.Name) and node.id in FUNCS and id(node) not in callee_names:
            raise EvalError(f"{node.id} can only be called")
        if isinstance(node, ast.Call):
            _check_call(node)
    return tree


def _allowed_constant(value: Any) -> bool:
    if isinstance(value, bool | str):
        return True
    return isinstance(value, int | float) and math.isfinite(value)


def _check_call(node: ast.Call) -> None:
    if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
        raise EvalError(f"only {FUNCS} may be called, with positional arguments")
    name, args = node.func.id, node.args
    if any(isinstance(a, ast.Starred) for a in args):
        raise EvalError("starred arguments are not allowed")
    if name == "threshold" and not (args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)):
        raise EvalError("threshold() needs a literal name first")
    if name == "abs" and len(args) != 1:
        raise EvalError("abs() takes one argument")
    if name in ("min", "max") and len(args) < 2:
        raise EvalError(f"{name}() needs at least two arguments")


def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, str):
        return "text"
    if isinstance(value, list):
        return "list"
    if isinstance(value, int | float) or hasattr(value, "magnitude"):
        return "number"
    return "other"


def _offset(value: Any) -> bool:
    return hasattr(value, "units") and units.is_offset_unit(value.units)


class Evaluator:
    """Walks a parsed expression. `read(name)` supplies input values; `threshold(args)` table values."""

    def __init__(self, read: Callable[[str], Any], threshold: Callable[[list[Any]], Any]) -> None:
        self._read = read
        self._threshold = threshold
        self._steps = 0
        self._active = 0

    def evaluate(self, expr: str) -> Any:
        """Evaluate one expression; the step budget covers the whole call including nested lookups."""
        outermost = self._active == 0
        if outermost:
            self._steps = 0
        self._active += 1
        try:
            return self._visit(parse(expr).body)
        finally:
            self._active -= 1

    def _visit(self, node: ast.expr) -> Any:
        self._steps += 1
        if self._steps > MAX_STEPS:
            raise Missing("budget", "evaluation took too many steps")
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            if node.id in ("True", "False"):
                return node.id == "True"
            return self._read(node.id)
        if isinstance(node, ast.List | ast.Tuple):
            return [self._visit(e) for e in node.elts]
        if isinstance(node, ast.BoolOp):
            is_and = isinstance(node.op, ast.And)
            for operand in node.values:
                value = self._visit(operand)
                if not isinstance(value, bool):
                    raise Missing("type", "and/or need true/false operands")
                if value != is_and:
                    return value
            return is_and
        if isinstance(node, ast.UnaryOp):
            value = self._visit(node.operand)
            if isinstance(node.op, ast.Not):
                if not isinstance(value, bool):
                    raise Missing("type", "'not' needs a true/false operand")
                return not value
            self._numeric(value)
            self._no_offset(value)
            return self._finite(-value)
        if isinstance(node, ast.BinOp):
            left, right = self._visit(node.left), self._visit(node.right)
            for side in (left, right):
                self._numeric(side)
                self._no_offset(side)
            try:
                return self._finite(_ARITH[type(node.op)](left, right))
            except ZeroDivisionError as exc:
                raise Missing("arithmetic", "division by zero") from exc
            except (OverflowError, ValueError, ArithmeticError) as exc:
                raise Missing("arithmetic", str(exc)) from exc
            except Exception as exc:
                raise Missing("unit", str(exc)) from exc
        if isinstance(node, ast.Compare):
            return self._compare(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        raise EvalError(f"{type(node).__name__} is not allowed")

    @staticmethod
    def _numeric(value: Any) -> None:
        if _kind(value) != "number":
            raise Missing("type", "a number is required")

    @staticmethod
    def _no_offset(value: Any) -> None:
        if _offset(value):
            raise Missing("unit", "absolute temperatures (degC/degF) cannot be used in arithmetic")

    @staticmethod
    def _finite(value: Any) -> Any:
        magnitude = value.magnitude if hasattr(value, "magnitude") else value
        if isinstance(magnitude, float) and not math.isfinite(magnitude):
            raise Missing("arithmetic", "result is not finite")
        return value

    def _compare(self, node: ast.Compare) -> bool:
        left = self._visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self._visit(comparator)
            if isinstance(op, ast.In | ast.NotIn):
                if _kind(right) != "list":
                    raise Missing("type", "'in' needs a list")
                for element in right:
                    self._comparable(left, element)
                found = any(self._equal(left, element) for element in right)
                ok = found if isinstance(op, ast.In) else not found
            else:
                self._comparable(left, right)
                try:
                    ok = bool(_CMP[type(op)](left, right))
                except Exception as exc:
                    raise Missing("unit", str(exc)) from exc
            if not ok:
                return False
            left = right
        return True

    def _equal(self, a: Any, b: Any) -> bool:
        try:
            return bool(a == b)
        except Exception as exc:
            raise Missing("unit", str(exc)) from exc

    @staticmethod
    def _comparable(a: Any, b: Any) -> None:
        if _kind(a) != _kind(b) or _kind(a) in ("other", "list"):
            raise Missing("type", f"cannot compare {_kind(a)} with {_kind(b)}")
        if _kind(a) == "number":
            qa, qb = hasattr(a, "units"), hasattr(b, "units")
            quantity = a if qa else b
            if qa != qb and not quantity.dimensionless:
                raise Missing("unit", "a quantity with units cannot be compared with a bare number")
            if qa and qb and a.units != b.units and (_offset(a) or _offset(b)):
                raise Missing("unit", "absolute temperatures in different units are not compared")

    def _call(self, node: ast.Call) -> Any:
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        if name == "threshold":
            args = [self._visit(a) for a in node.args]
            return self._threshold(args)
        args = [self._visit(a) for a in node.args]
        for value in args:
            self._numeric(value)
            self._no_offset(value)
        try:
            if name == "abs":
                return abs(args[0])
            for other in args[1:]:
                self._comparable(args[0], other)
            return min(args) if name == "min" else max(args)
        except Missing:
            raise
        except Exception as exc:
            raise Missing("unit", str(exc)) from exc
