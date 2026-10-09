#!/usr/bin/env python3
"""Validate rule YAML files against rules/schema/rule.schema.json plus project checks.

Usage: python3 scripts/validate_rules.py [files...]   (no args = all rules)
Needs: pyyaml, jsonschema, pint.

Project checks beyond the schema: edition/state/id consistency, forbidden wording keys,
whitelisted expressions, pint dimension analysis of every expression (a bare number is never
compared with a quantity), unit-bearing thresholds, the exemption convention and the
applies_when input rule (see docs/engine-conventions.md).
"""
import ast
import itertools
import json
import sys
import unicodedata
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

try:
    import yaml
    from jsonschema import Draft202012Validator
except ImportError:
    print("validate_rules: install pyyaml, jsonschema and pint (uv sync --group dev)", file=sys.stderr)
    sys.exit(0)  # don't block edits before deps exist; scripts/ci.sh fails if they are missing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "apps" / "api"))
try:
    from mep.engine import units as engine_units
    from mep.engine.units import valid_unit_text
except ImportError:  # pint missing
    print("validate_rules: install pint (uv sync --group dev)", file=sys.stderr)
    sys.exit(0)

SCHEMA = ROOT / "rules" / "schema" / "rule.schema.json"

FORBIDDEN_KEYS = {"clause_text", "standard_text", "verbatim"}
ALLOWED_FUNCS = {"threshold", "min", "max", "abs"}
BARE_NAMES = {"True", "False"}
STRUCTURAL_KEYS = {"edition", "state"}  # project-level selectors, validated separately
EXPR_LIST_KEYS = ("exempt_when", "needs_judgement_when")
ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.BinOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE,
    ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Name, ast.Load, ast.Constant, ast.Call,
    ast.List, ast.Tuple,
)
OPAQUE = ("o", None)  # unusable operand (after an error)
BOOL = ("b", None)
TEXT = ("s", None)
LIST = ("l", None)
NONNUM = ("b", "s", "o", "l")


def visible(value: object) -> str:
    """Text with control and zero-width characters removed (an invisible name is no name)."""
    return "".join(c for c in str(value or "") if unicodedata.category(c) not in ("Cc", "Cf")).strip()


def walk_keys(node: object) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in walk_keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in walk_keys(v)}
    return set()


def dims(unit: str) -> Any:
    """Dimensionality via the engine's safe unit parser (never pint's string parser)."""
    return engine_units.dimensionality(unit)


def is_offset(unit: str) -> bool:
    return engine_units.is_offset(unit)


def parse_unit(unit: object, where: str, errors: list[str]) -> Any:
    if not valid_unit_text(unit):  # never hand odd text (digit separators, huge exponents) to pint
        errors.append(f"{where}: '{unit}' is not plain unit text (NAME, one single-digit ^exponent, * or /)")
        return None
    try:
        return dims(str(unit))
    except Exception:  # noqa: BLE001 - pint raises several error types
        errors.append(f"{where}: '{unit}' is not a parseable pint unit")
        return None


# ---- threshold tree -----------------------------------------------------------------------

def threshold_leaves(node: Any, inherited: str | None, path: str) -> Iterator[tuple[str, str | None]]:
    """Yield (path, unit) for every numeric leaf under a threshold node."""
    if isinstance(node, dict):
        if "formula" in node:
            return
        if "value" in node:
            yield path, node.get("unit", inherited)
            return
        inner = node["values"] if "values" in node and "unit" in node else node
        unit = node.get("unit", inherited) if "values" in node else inherited
        for key, child in inner.items():
            yield from threshold_leaves(child, unit, f"{path}.{key}")
    else:
        yield path, inherited


def lookup_units(node: Any, inherited: str | None, args: list[ast.expr]) -> set[str | None]:
    """Units reachable by threshold(*args); runtime (Name) keys fan out over all children."""
    if isinstance(node, dict) and "formula" in node:
        return {node["unit"]} if not args else {"__extra__"}
    if isinstance(node, dict) and "value" in node:
        return {node.get("unit", inherited)} if not args else {"__extra__"}
    if isinstance(node, dict):
        inner = node["values"] if "values" in node and "unit" in node else node
        unit = node.get("unit", inherited) if "values" in node else inherited
        if not args:
            return {"__incomplete__"}
        head, rest = args[0], args[1:]
        if isinstance(head, ast.Constant):
            if head.value not in inner:
                return {"__missing__"}
            return lookup_units(inner[head.value], unit, rest)
        out: set[str | None] = set()
        for child in inner.values():
            out |= lookup_units(child, unit, rest)
        return out
    return {inherited} if not args else {"__extra__"}


# ---- expressions --------------------------------------------------------------------------

class Dimensioner:
    """Whitelist + pint dimension analysis. No eval: walks the AST only."""

    def __init__(self, inputs: dict[str, dict[str, Any]], thr: dict[str, Any], where: str):
        self.inputs, self.thr, self.where = inputs, thr, where
        self.errors: list[str] = []
        self.used: set[str] = set()

    def err(self, msg: str) -> None:
        self.errors.append(f"{self.where}: {msg}")

    def run(self, expr: str) -> tuple[str, Any]:
        try:
            tree = ast.parse(expr.strip(), mode="eval")
        except (SyntaxError, RecursionError, ValueError) as exc:
            self.err(f"does not parse: {exc.msg}")
            return OPAQUE
        for node in ast.walk(tree):
            if not isinstance(node, ALLOWED_NODES):
                self.err(f"disallowed syntax {type(node).__name__}")
                return OPAQUE
        return self.visit(tree.body)

    def run_bool(self, expr: str) -> None:
        before = len(self.errors)
        kind, _ = self.run(expr)
        if kind != "b" and len(self.errors) == before:
            self.err("must evaluate to true/false (a comparison or boolean expression)")
        if len(self.errors) == before and not self.used:
            self.err("must read at least one input (a constant condition decides nothing)")

    def run_quantity(self, expr: str) -> tuple[str, Any]:
        kind, d = self.run(expr)
        if kind not in ("q", "o"):
            self.err("must evaluate to a quantity with units")
        return kind, d

    def visit(self, node: ast.expr) -> tuple[str, Any]:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return BOOL
            if isinstance(node.value, str):
                return TEXT
            return ("c", None)
        if isinstance(node, ast.Name):
            if node.id in BARE_NAMES:
                return BOOL
            if node.id in ALLOWED_FUNCS:
                self.err(f"'{node.id}' used as a name")
                return OPAQUE
            inp = self.inputs.get(node.id)
            self.used.add(node.id)
            if inp is None:
                self.err(f"unknown name '{node.id}' (not a declared input)")
                return OPAQUE
            if inp.get("unit"):
                try:
                    return ("q", dims(inp["unit"]))
                except Exception:  # noqa: BLE001 - reported once by the input unit check
                    return OPAQUE
            if inp.get("type") in ("int", "number"):
                return ("q", dims("dimensionless"))
            return BOOL if inp.get("type") == "bool" else TEXT
        if isinstance(node, (ast.List, ast.Tuple)):
            for elt in node.elts:
                self.visit(elt)
            return LIST
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                if self.visit(v)[0] != "b":
                    self.err("and/or operands must be true/false expressions")
            return BOOL
        if isinstance(node, ast.UnaryOp):
            inner = self.visit(node.operand)
            if isinstance(node.op, ast.USub) and self.has_offset(node.operand):
                self.err("absolute temperatures (degC/degF) cannot be negated")
            if isinstance(node.op, ast.Not):
                if inner[0] != "b":
                    self.err("'not' needs a true/false operand")
                return BOOL
            return inner
        if isinstance(node, ast.BinOp):
            return self.binop(node)
        if isinstance(node, ast.Compare):
            return self.compare(node)
        if isinstance(node, ast.Call):
            return self.call(node)
        self.err(f"disallowed syntax {type(node).__name__}")
        return OPAQUE

    def binop(self, node: ast.BinOp) -> tuple[str, Any]:
        if self.has_offset(node.left, node.right):
            self.err("absolute temperatures (degC/degF) cannot be used in arithmetic: a difference "
                     "of two absolute temperatures is not an absolute temperature; use a K input")
        lk, ld = self.visit(node.left)
        rk, rd = self.visit(node.right)
        if lk in NONNUM or rk in NONNUM:
            if "o" not in (lk, rk):
                self.err("arithmetic on a non-numeric operand")
            return OPAQUE
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if lk == "c" and rk == "c":
                return ("c", None)
            if lk == "c" or rk == "c":
                other = rd if lk == "c" else ld
                if other != dims("dimensionless"):
                    self.err("adding a bare number to a quantity")
                return ("q", other)
            if ld != rd:
                self.err(f"cannot add/subtract {ld} and {rd}")
            return ("q", ld)
        ld = dims("dimensionless") if lk == "c" else ld
        rd = dims("dimensionless") if rk == "c" else rd
        return ("q", ld * rd if isinstance(node.op, ast.Mult) else ld / rd)

    def has_offset(self, *nodes: ast.expr) -> bool:
        return any(is_offset(u) for n in nodes for u in self.side_units(n))

    def side_units(self, node: ast.expr) -> set[str]:
        out: set[str] = set()
        for sub_node in ast.walk(node):
            out |= self.unit_set(sub_node) or set()
        return out

    def unit_set(self, node: ast.expr) -> set[str] | None:
        if isinstance(node, ast.Name) and node.id in self.inputs and self.inputs[node.id].get("unit"):
            return {self.inputs[node.id]["unit"]}
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "threshold"
                and node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in self.thr):
            units = lookup_units(self.thr[node.args[0].value], None, list(node.args[1:]))
            return {u for u in units if u and not u.startswith("__")} or None
        return None

    def check_literal_domains(self, node: ast.Compare) -> None:
        """A text literal compared with an enum input must be one of the input's declared values."""
        operands = [node.left, *node.comparators]
        for left, right in itertools.pairwise(operands) if len(operands) > 1 else []:
            for name_node, other in ((left, right), (right, left)):
                if not (isinstance(name_node, ast.Name) and name_node.id in self.inputs):
                    continue
                domain = self.inputs[name_node.id].get("values")
                if not domain:
                    continue
                lits = ([e.value for e in other.elts if isinstance(e, ast.Constant)]
                        if isinstance(other, (ast.List, ast.Tuple))
                        else [other.value] if isinstance(other, ast.Constant) else [])
                for lit in lits:
                    if isinstance(lit, str) and lit not in domain:
                        self.err(f"'{lit}' is not in the declared values of input '{name_node.id}'")

    def compare(self, node: ast.Compare) -> tuple[str, Any]:
        nodes = [node.left, *node.comparators]
        sides = [self.visit(n) for n in nodes]
        membership = [isinstance(op, (ast.In, ast.NotIn)) for op in node.ops]
        if any(membership):
            if len(node.ops) != 1:
                self.err("membership tests cannot be chained")
                return BOOL
            if not isinstance(node.comparators[0], (ast.List, ast.Tuple)):
                self.err("'in' needs a literal list on the right")
                return BOOL
            if self.has_offset(node.left, *node.comparators[0].elts):
                self.err("absolute temperatures (degC/degF) cannot be used in membership tests")
            elems = [self.visit(e) for e in node.comparators[0].elts]
            pairs = [(sides[0], e) for e in elems]
            unit_pairs: list[tuple[ast.expr, ast.expr]] = []
        else:
            pairs = list(itertools.pairwise(sides))
            unit_pairs = list(itertools.pairwise(nodes))
        self.check_literal_domains(node)
        for (lk, ld), (rk, rd) in pairs:
            if "o" in (lk, rk):
                continue
            numeric = {"q", "c"}
            if (lk in numeric) != (rk in numeric) or (lk not in numeric and lk != rk):
                self.err(f"compares incompatible types ({lk} vs {rk}): numbers, text and true/false never mix")
            elif lk == "c" and rk == "c":
                continue
            elif "c" in (lk, rk):
                other = ld if lk == "q" else rd
                if other != dims("dimensionless"):
                    self.err("bare number compared with a quantity that has units: use a threshold")
            elif lk == "q" and ld != rd:
                self.err(f"compares incompatible dimensions {ld} and {rd}")
        for ln, rn in unit_pairs:  # absolute (degC/degF) and difference (K) temperatures never mix
            lu, ru = self.side_units(ln), self.side_units(rn)
            if lu and ru and any(is_offset(u) for u in lu | ru) and lu != ru:
                self.err(f"compares {sorted(lu)} with {sorted(ru)}: offset temperature units must match exactly")
        return BOOL

    def call(self, node: ast.Call) -> tuple[str, Any]:
        if not (isinstance(node.func, ast.Name) and node.func.id in ALLOWED_FUNCS and not node.keywords):
            self.err(f"only {sorted(ALLOWED_FUNCS)} may be called")
            return OPAQUE
        name = node.func.id
        if name == "threshold":
            first = node.args[0] if node.args else None
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                self.err("threshold() first argument must be a string literal name")
                return OPAQUE
            if first.value not in self.thr:
                self.err(f"threshold('{first.value}') is not defined")
                return OPAQUE
            for a in node.args[1:]:
                self.visit(a)
            units = lookup_units(self.thr[first.value], None, list(node.args[1:]))
            if "__missing__" in units:
                self.err(f"threshold path under '{first.value}' has a key that does not exist")
                return OPAQUE
            if "__incomplete__" in units or "__extra__" in units:
                self.err(f"threshold('{first.value}', ...) must name exactly one value (path incomplete or too long)")
                return OPAQUE
            ds = {parse_unit(u, self.where, self.errors) for u in units if u}
            if None in units or len(ds - {None}) != 1:
                self.err(f"threshold('{first.value}') does not resolve to one explicit unit")
                return OPAQUE
            (only,) = ds - {None}
            if only == dims("dimensionless"):
                self.err(f"threshold('{first.value}') is dimensionless; thresholds must carry physical units")
            return ("q", only)
        if self.has_offset(*node.args) and name != "threshold":
            self.err(f"absolute temperatures (degC/degF) cannot be passed to {name}()")
        if name in ("min", "max") and len(node.args) < 2:
            self.err(f"{name}() needs at least two arguments")
        parts = [self.visit(a) for a in node.args]
        if any(k == "o" for k, _ in parts):
            return OPAQUE
        qd = {d for k, d in parts if k == "q"}
        if len(qd) > 1 or (qd and any(k == "c" for k, _ in parts)):
            self.err(f"{name}() mixes incompatible operands")
        return ("q", next(iter(qd))) if qd else ("c", None)


# ---- per-file checks ----------------------------------------------------------------------

def formula_cycles(values: dict[str, Any], path: Path) -> list[str]:
    refs: dict[str, set[str]] = {}
    for name, entry in values.items():
        if isinstance(entry, dict) and "formula" in entry:
            try:
                tree = ast.parse(entry["formula"].strip(), mode="eval")
            except SyntaxError:
                continue
            refs[name] = {
                n.args[0].value for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "threshold"
                and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value in values
            }
    errors: list[str] = []

    for name, direct in refs.items():
        stack, seen = list(direct), set()
        while stack:
            cur = stack.pop()
            if cur == name:
                errors.append(f"{path}: threshold formula '{name}' refers to itself")
                break
            if cur not in seen:
                seen.add(cur)
                stack.extend(refs.get(cur, set()))
    return errors


def check_thresholds(rule: dict[str, Any], inputs: dict[str, dict[str, Any]], path: Path) -> list[str]:
    errors: list[str] = []
    thr = rule.get("threshold") or {}
    values = thr.get("values")
    if values is None:
        return errors
    if not isinstance(values, dict):
        return [f"{path}: threshold.values must be a map of unit-bearing entries"]
    for p, unit in threshold_leaves(values, None, "values"):
        if not unit:
            errors.append(f"{path}: threshold {p} has no explicit unit")
        else:
            got = parse_unit(unit, f"{path}: threshold {p}", errors)
            if got is not None and got == dims("dimensionless"):
                errors.append(f"{path}: threshold {p} is dimensionless; thresholds must carry physical units")
    for name, entry in values.items():
        if isinstance(entry, dict) and "formula" in entry:
            d = Dimensioner(inputs, values, f"{path}: threshold formula '{name}'")
            kind, got = d.run_quantity(entry["formula"])
            errors += d.errors
            want = parse_unit(entry["unit"], f"{path}: threshold formula '{name}'", errors)
            if is_offset(entry["unit"]):
                errors.append(f"{path}: threshold formula '{name}' cannot be an absolute temperature unit")
            if kind == "q" and want is not None and got != want:
                errors.append(f"{path}: threshold formula '{name}' has dimension {got}, declared {want}")
            if kind == "c":
                errors.append(f"{path}: threshold formula '{name}' has no operands with units")
    errors += formula_cycles(values, path)
    return errors


def check_file(path: Path, validator: Draft202012Validator) -> list[str]:
    errors: list[str] = []
    try:
        rule = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"{path}: YAML error: {exc}"]

    if isinstance(rule, dict):  # YAML turns an unquoted 2026-10-07 into a date object: the schema wants text
        for field in ("reviewed_on", "checked_on"):
            if isinstance(rule.get(field), date):
                rule[field] = rule[field].isoformat()

    for err in validator.iter_errors(rule):
        loc = "/".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{path}: {loc}: {err.message}")

    if not isinstance(rule, dict):
        return errors

    if path.stem != rule.get("id"):
        errors.append(f"{path}: filename must equal rule id ({rule.get('id')})")

    aw = rule.get("applies_when", {}) or {}
    edition = aw.get("edition", "")

    if rule.get("status") == "approved":
        for field in ("reviewed_by", "reviewed_on", "reviewer_registration_no"):
            if not visible(rule.get(field)):
                errors.append(f"{path}: approved rules need {field}")
        if "TODO" in path.read_text(encoding="utf-8"):
            errors.append(f"{path}: approved rules cannot contain TODO values")

    if rule.get("status") != "approved" and any(
            visible(rule.get(f)) for f in ("reviewed_by", "reviewed_on", "reviewer_registration_no")):
        errors.append(f"{path}: reviewer sign-off fields on a rule that is not approved (only the engineer "
                      "approves, by setting status together with all three fields)")
    checked = (bool(visible(rule.get("checked_by"))), bool(visible(rule.get("checked_on"))))
    if checked[0] != checked[1]:
        errors.append(f"{path}: checked_by and checked_on go together (a non-approving first-pass value check)")

    inputs_list = [i for i in rule.get("inputs", []) if isinstance(i, dict) and "name" in i]
    inputs = {i["name"]: i for i in inputs_list}
    input_names = set(inputs)
    dep_leaves = {d.rsplit(".", 1)[-1] for d in rule.get("depends_on", []) if isinstance(d, str)}
    for name in sorted(input_names - dep_leaves):
        errors.append(f"{path}: input '{name}' is not listed in depends_on")

    bad_keys = walk_keys(rule) & FORBIDDEN_KEYS
    if bad_keys:
        errors.append(f"{path}: standards wording keys are forbidden: {sorted(bad_keys)}")

    outcomes = rule.get("outcomes", {})
    if outcomes.get("true") is not None and outcomes.get("true") == outcomes.get("false"):
        errors.append(f"{path}: outcomes true/false must differ")

    # edition consistency: id, applies_when, source and directory must agree (rule 7)
    rule_id = str(rule.get("id", ""))
    if edition and rule_id[:7] != edition:
        errors.append(f"{path}: id edition {rule_id[:7]} != applies_when.edition {edition}")
    src_ed = str(rule.get("source", {}).get("edition", ""))
    if edition and src_ed and f"NCC{src_ed}" != edition:
        errors.append(f"{path}: source.edition {src_ed} != applies_when.edition {edition}")
    parts = path.parts
    if "rules" in parts and edition:
        below = parts[parts.index("rules") + 1 :]
        if len(below) != 3 or below[0] != edition.lower():
            errors.append(f"{path}: must be rules/{edition.lower()}/<part>/<id>.yaml")

    states = aw.get("state", [])
    id_bits = rule_id.split("-")
    state_id = id_bits[1] if len(id_bits) >= 4 and len(id_bits[1]) <= 3 else None
    if state_id and states != [state_id]:
        errors.append(f"{path}: state-variation id {state_id} requires state: [{state_id}]")
    if not states or ("ALL" in states and len(states) > 1):
        errors.append(f"{path}: state must be [ALL] or a non-empty list of states")

    # units: every numeric input carries a parseable pint unit
    for i in inputs_list:
        if i.get("type", "number") == "number" and not i.get("unit"):
            errors.append(f"{path}: numeric input '{i['name']}' needs a unit")
        if i.get("unit"):
            got = parse_unit(i["unit"], f"{path}: input '{i['name']}'", errors)
            if got is not None and got == dims("dimensionless") and i["unit"] != "dimensionless":
                errors.append(f"{path}: input '{i['name']}' uses a scaled dimensionless unit '{i['unit']}'; "
                              "store it as a plain ratio with unit 'dimensionless'")
    errors += check_thresholds(rule, inputs, path)
    thr_values = (rule.get("threshold") or {}).get("values") or {}

    # applies_when: every key (bar edition/state and the expression lists) is a declared input
    for key, spec in aw.items():
        if key in STRUCTURAL_KEYS or key in EXPR_LIST_KEYS:
            continue
        if key not in input_names:
            errors.append(f"{path}: applies_when key '{key}' must be a declared input")
        elif not inputs[key].get("provenance"):
            errors.append(f"{path}: applies_when key '{key}' has no provenance")
        elif inputs[key].get("type") == "bool":
            errors.append(f"{path}: applies_when '{key}' is a true/false input; use exempt_when or "
                          "needs_judgement_when so an unencoded exemption cannot hide as NOT_APPLICABLE")
        elif inputs[key].get("unit"):
            errors.append(f"{path}: applies_when '{key}' has a unit; filters are for enum/text/int inputs only")
        spec_vals = spec if isinstance(spec, list) else (
            [spec] if not isinstance(spec, dict) else [*spec.get("in", []), *spec.get("not_in", [])])
        domain = inputs.get(key, {}).get("values")
        if key in input_names and inputs[key].get("type") in ("enum", "string"):
            if not domain:
                errors.append(f"{path}: applies_when '{key}' needs a closed `values` domain on the input")
            elif not all(isinstance(v, str) for v in spec_vals) or not set(spec_vals) <= set(domain):
                errors.append(f"{path}: applies_when '{key}' lists values outside the input's declared domain")
        if isinstance(spec, bool):
            errors.append(
                f"{path}: applies_when '{key}: {spec}' is a boolean exemption filter; put it in "
                "exempt_when (encoded exemption) or needs_judgement_when (not encoded)"
            )
        if isinstance(spec, dict) and not set(spec) <= {"in", "not_in"}:
            errors.append(f"{path}: applies_when '{key}' may only use in / not_in (found {sorted(spec)})")
    for key in EXPR_LIST_KEYS:
        if key in rule:
            errors.append(f"{path}: {key} belongs inside applies_when")

    # expressions: whitelist + dimensions
    exprs = [("check", rule.get("check", ""))]
    for key in EXPR_LIST_KEYS:
        exprs += [(f"{key}[{n}]", str(e)) for n, e in enumerate(aw.get(key, []) or [])]
    used: set[str] = set()
    for where, expr in exprs:
        d = Dimensioner(inputs, thr_values, f"{path}: {where}")
        d.run_bool(str(expr))
        errors += d.errors
        used |= d.used
    for name in sorted(used & input_names):
        if inputs[name].get("type") in ("enum", "string") and not inputs[name].get("values"):
            errors.append(f"{path}: enum/string input '{name}' is read by an expression and needs a closed "
                          "`values` domain (unknown values must give NEEDS_JUDGEMENT)")

    # tests
    tests = rule.get("tests", [])
    for n, test in enumerate(tests):
        extra = set(test.get("inputs", {})) - input_names
        if extra:
            errors.append(f"{path}: tests[{n}] uses undeclared inputs {sorted(extra)}")
    expects = {t.get("expect") for t in tests}
    if aw.get("exempt_when") and "NOT_APPLICABLE" not in expects:
        errors.append(f"{path}: exempt_when needs a test expecting NOT_APPLICABLE")
    if aw.get("needs_judgement_when") and "NEEDS_JUDGEMENT" not in expects:
        errors.append(f"{path}: needs_judgement_when needs a test expecting NEEDS_JUDGEMENT")

    for hyp in rule.get("fix_hypotheses", []):
        if not str(hyp).startswith("Hypothesis: verify"):
            errors.append(f"{path}: fix hypotheses must start 'Hypothesis: verify'")

    return errors


def main(argv: list[str]) -> int:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text()))
    files = [Path(a) for a in argv] or sorted((ROOT / "rules").rglob("*.yaml"))
    linked = [str(f) for f in files if f.is_symlink() or any(p.is_symlink() for p in f.parents if ROOT in p.parents)]
    if linked:
        print("rule files and folders must not be symbolic links: " + ", ".join(linked), file=sys.stderr)
        return 1
    files = [f for f in files if "schema" not in f.parts and f.name not in ("adoption.yaml", "applicability.yaml")]
    all_errors = [e for f in files for e in check_file(f, validator)]
    for e in all_errors:
        print(e, file=sys.stderr)
    if not all_errors:
        print(f"{len(files)} rule file(s) valid")
        if not argv:  # whole-pack run: say which rules cannot be selected without an approver override
            import reachability  # local import: only needed here

            for line in reachability.lines(reachability.analyse()):
                print(line)
    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
