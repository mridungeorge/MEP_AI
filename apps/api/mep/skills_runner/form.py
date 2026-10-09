"""A skill's spec card (JSON Schema) as a plain description of a form, and firm defaults merged into a spec.

The web wizard draws exactly what this returns, so the required fields, their units, their limits and which ones may take a firm default all
come from the card itself (`required`, `x-unit`, `minimum`/`maximum`, `x-firm-default`): nothing about a skill is hard-coded in the UI.

Field kinds: number, integer, text, enum, group (a nested object), list (repeating group), choice (one of several object shapes picked
by a `type` key), conditional (a group chosen by another field's value). Nothing here evaluates anything or decides compliance.
"""
import copy
import re
from typing import Any

Schema = dict[str, Any]
Field = dict[str, Any]


def _resolve(node: Schema, root: Schema) -> Schema:
    """Follow a local $ref; sibling keywords (a description next to the $ref) override the target's."""
    seen = 0
    while "$ref" in node and seen < 20:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise ValueError(f"only local $ref is supported: {ref}")
        target: Any = root
        for part in ref[2:].split("/"):
            target = target[part]
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        seen += 1
    return node


def _label(name: str) -> str:
    base = re.sub(r"_(mm|m2|kw|ls)$", "", name)
    return base.replace("_", " ").strip().capitalize()


def _unit(name: str, node: Schema) -> str | None:
    if "x-unit" in node:
        return str(node["x-unit"])
    m = re.search(r"_(mm|m2|kw|ls)$", name)
    return {"mm": "mm", "m2": "m2", "kw": "kW", "ls": "L/s"}[m.group(1)] if m else None


def _field(path: str, name: str, node: Schema, required: bool, root: Schema, spec: Schema | None) -> Field | None:
    node = _resolve(node, root)
    base: Field = {"path": path, "name": name, "label": _label(name), "required": required, "help": node.get("description", ""),
                   "firm_default": bool(node.get("x-firm-default", False))}
    if "const" in node:
        return None                                              # fixed by the card (spec_version, units): the builder fills it in
    if "enum" in node:
        return {**base, "kind": "enum", "options": list(node["enum"]), "default": node.get("default")}
    t = node.get("type")
    if t in ("number", "integer"):
        return {**base, "kind": t, "unit": _unit(name, node), "min": node.get("minimum"), "max": node.get("maximum"),
                "step": node.get("multipleOf"), "default": node.get("default")}
    if t == "string":
        return {**base, "kind": "text", "pattern": node.get("pattern"), "max_length": node.get("maxLength")}
    if t == "array":
        item = _resolve(node["items"], root)
        if item.get("type") == "array":                             # a list of [x, y] points: one "x, y" pair per line
            return {**base, "kind": "points", "min_items": node.get("minItems"), "max_items": node.get("maxItems"), "unit": "mm"}
        return {**base, "kind": "list", "min_items": node.get("minItems", 0), "max_items": node.get("maxItems"),
                "item": object_fields(item, root, f"{path}[]", spec)}
    if t == "object" and "oneOf" not in node:
        return {**base, "kind": "group", "fields": object_fields(node, root, path, spec)}
    if "oneOf" in node:
        cases: dict[str, list[Field]] = {}
        for option in node["oneOf"]:
            option = _resolve(option, root)
            kind = option["properties"]["type"]["const"]
            cases[kind] = object_fields(option, root, path, spec, skip=("type",))
        return {**base, "kind": "choice", "key": f"{path}.type", "cases": cases}
    return None


def object_fields(node: Schema, root: Schema, prefix: str = "", spec: Schema | None = None, skip: tuple[str, ...] = ()) -> list[Field]:
    node = _resolve(node, root)
    required = set(node.get("required", []))
    out: list[Field] = []
    for name, sub in node.get("properties", {}).items():
        if name in skip:
            continue
        path = f"{prefix}.{name}" if prefix else name
        if name == "geometry" and "allOf" in root:                 # duct-fab: the geometry fields depend on the fitting
            out.append(_conditional(path, name, root, spec))
            continue
        f = _field(path, name, sub, name in required, root, spec)
        if f is not None:
            out.append(f)
    return out


def _conditional(path: str, name: str, root: Schema, spec: Schema | None) -> Field:
    cases: dict[str, list[Field]] = {}
    key = None
    for rule in root.get("allOf", []):
        cond = rule.get("if", {}).get("properties", {})
        for k, v in cond.items():
            if "const" in v and "geometry" in rule.get("then", {}).get("properties", {}):
                key = k
                cases[v["const"]] = object_fields(rule["then"]["properties"]["geometry"], root, path, spec)
    return {"path": path, "name": name, "label": "Geometry", "required": True, "kind": "conditional", "on": key or "fitting", "cases": cases,
            "help": "", "firm_default": False}


def build_form(schema: Schema) -> list[Field]:
    return object_fields(schema, schema)


# ---------------------------------------------------------------------------------------------------- firm defaults
def default_paths(form: list[Field], spec: Schema | None = None) -> set[str]:
    """The dotted paths a firm default may be stored for (`x-firm-default` fields), for the variants the spec has chosen (all if none)."""
    out: set[str] = set()

    def walk(fields: list[Field]) -> None:
        for f in fields:
            if f.get("firm_default"):
                out.add(f["path"])
            if f["kind"] == "group":
                walk(f["fields"])
            elif f["kind"] == "list":
                walk(f["item"])
            elif f["kind"] in ("choice", "conditional"):
                for case in f["cases"].values():
                    walk(case)

    walk(form)
    return out


def _get(d: Any, parts: list[str]) -> Any:
    for p in parts:
        if not isinstance(d, dict) or p not in d:
            return None
        d = d[p]
    return d


def apply_defaults(schema: Schema, spec: Schema, defaults: Schema) -> tuple[Schema, list[str]]:
    """Fill `x-firm-default` fields the spec leaves empty from the firm's defaults. Returns (new spec, the paths that were filled).
    Top-level and per-room defaults are applied; a default for a field that the chosen variant does not have is skipped."""
    form = build_form(schema)
    allowed = default_paths(form)
    out = copy.deepcopy(spec)
    filled: list[str] = []
    variant_paths = _variant_paths(form, spec)
    for path, value in defaults.items():
        if path not in allowed:
            continue
        if path.startswith("rooms[]."):                              # a per-room default
            sub = path.removeprefix("rooms[].")
            for i, room in enumerate(out.get("rooms") or []):
                if isinstance(room, dict) and room.get(sub) is None:
                    room[sub] = value
                    filled.append(f"rooms[{i}].{sub}")
            continue
        if path not in variant_paths:
            continue
        parts = path.split(".")
        if _get(out, parts) is None:
            node = out
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = value
            filled.append(path)
    return out, filled


def _variant_paths(form: list[Field], spec: Schema) -> set[str]:
    paths: set[str] = set()

    def walk(fields: list[Field]) -> None:
        for f in fields:
            paths.add(f["path"])
            if f["kind"] == "group":
                walk(f["fields"])
            elif f["kind"] == "conditional":
                chosen = _get(spec, f["on"].split("."))
                walk(f["cases"].get(chosen, []))

    walk(form)
    return paths
