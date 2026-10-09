"""Revision diff: what changed between a parent revision and its child, and which stored results that makes stale.

Pure functions over plain dicts (no database, no model): the same inputs always give the same answer, and the answer is built from
the two revisions' own rows and the rule graph only.

* spaces are matched by IFC GUID; the rest by name AND centroid (within a tolerance), or by name alone when the name is unique on both
  sides and a centroid is missing;
* a matched pair is `changed` only when a field moved by more than its tolerance (area, use, storey, ceiling void, name);
* system inputs are compared per system tag and input name (a change of the `building_part` input is a change of part);
* every change is turned into graph `Change`s, and a stored result is stale when a change reaches its rule along a graph edge (a
  change to one system's input only touches THAT system's results; project- and space-level changes touch every subject's).
"""
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from mep.diff.graph import Change, DependencyGraph, Edge

AREA_ABS_M2 = 0.01
AREA_REL = 0.005
VOID_ABS_MM = 5.0
CENTROID_TOL_M = 1.0
BUILDING_PART = "building_part"


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _num(v: Any) -> float | None:
    """The value as a number, or None for anything that is not one (text inputs, booleans, missing values)."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class FieldChange:
    field: str
    old: Any
    new: Any


@dataclass(frozen=True)
class SpaceDiff:
    change: str                               # added | removed | changed | unchanged
    key: str                                  # IFC GUID, else the name: how a human finds it
    old: Mapping[str, Any] | None
    new: Mapping[str, Any] | None
    fields: tuple[FieldChange, ...] = ()
    matched_by: str = ""                      # guid | name+centroid | name


@dataclass(frozen=True)
class InputDiff:
    change: str                               # added | removed | changed
    system: str
    name: str
    old: Any
    new: Any
    unit_old: str | None = None
    unit_new: str | None = None

    @property
    def is_part_change(self) -> bool:
        return self.name == BUILDING_PART


def _order(s: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(s.get("ifc_guid") or ""), _norm(s.get("name")), str(s.get("id") or "")


def _label(s: Mapping[str, Any]) -> str:
    return str(s.get("ifc_guid") or s.get("name") or s.get("id"))


def match_spaces(old: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]],
                 centroid_tol_m: float = CENTROID_TOL_M) -> tuple[list[tuple[Mapping[str, Any], Mapping[str, Any], str]],
                                                                  list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """(matched pairs with how they matched, removed, added). Deterministic: inputs are ordered before matching."""
    old_left, new_left = sorted(old, key=_order), sorted(new, key=_order)
    pairs: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []

    by_guid: dict[str, list[Mapping[str, Any]]] = {}
    for s in new_left:
        if s.get("ifc_guid"):
            by_guid.setdefault(str(s["ifc_guid"]), []).append(s)
    still_old: list[Mapping[str, Any]] = []
    taken: set[int] = set()
    for s in old_left:
        candidates = by_guid.get(str(s.get("ifc_guid") or ""), [])
        if s.get("ifc_guid") and candidates:
            n = candidates.pop(0)
            pairs.append((s, n, "guid"))
            taken.add(id(n))
        else:
            still_old.append(s)
    new_left = [s for s in new_left if id(s) not in taken]

    # the rest: same name AND centroid within tolerance (closest first); where either side has no centroid, same name only when
    # it is unambiguous (one candidate each side)
    candidates_pairs: list[tuple[float, int, int]] = []
    for i, a in enumerate(still_old):
        for j, b in enumerate(new_left):
            if _norm(a.get("name")) != _norm(b.get("name")) or not _norm(a.get("name")):
                continue
            d = _distance(a, b)
            if d is not None and d <= centroid_tol_m:
                candidates_pairs.append((d, i, j))
    used_o: set[int] = set()
    used_n: set[int] = set()
    for _d, i, j in sorted(candidates_pairs):
        if i not in used_o and j not in used_n:
            pairs.append((still_old[i], new_left[j], "name+centroid"))
            used_o.add(i)
            used_n.add(j)
    for i, a in enumerate(still_old):
        if i in used_o or _norm(a.get("name")) == "":
            continue
        same_o = [k for k, x in enumerate(still_old) if k not in used_o and _norm(x.get("name")) == _norm(a.get("name"))]
        same_n = [k for k, x in enumerate(new_left) if k not in used_n and _norm(x.get("name")) == _norm(a.get("name"))]
        if len(same_o) == 1 and len(same_n) == 1 and (_distance(a, new_left[same_n[0]]) is None):
            pairs.append((a, new_left[same_n[0]], "name"))
            used_o.add(i)
            used_n.add(same_n[0])
    removed = [a for i, a in enumerate(still_old) if i not in used_o]
    added = [b for j, b in enumerate(new_left) if j not in used_n]
    return pairs, removed, added


def _distance(a: Mapping[str, Any], b: Mapping[str, Any]) -> float | None:
    ax, ay, bx, by = (_num(a.get("centroid_x_m")), _num(a.get("centroid_y_m")),
                      _num(b.get("centroid_x_m")), _num(b.get("centroid_y_m")))
    if None in (ax, ay, bx, by):
        return None
    return math.hypot(ax - bx, ay - by)  # type: ignore[operator]


def _space_fields(a: Mapping[str, Any], b: Mapping[str, Any]) -> tuple[FieldChange, ...]:
    out: list[FieldChange] = []
    aa, ab = _num(a.get("area_m2")), _num(b.get("area_m2"))
    if (aa is None) != (ab is None) or (aa is not None and ab is not None
                                         and abs(aa - ab) > max(AREA_ABS_M2, AREA_REL * max(abs(aa), abs(ab)))):
        out.append(FieldChange("area_m2", a.get("area_m2"), b.get("area_m2")))
    if _norm(a.get("use")) != _norm(b.get("use")):
        out.append(FieldChange("use", a.get("use"), b.get("use")))
    if _norm(a.get("storey")) != _norm(b.get("storey")):
        out.append(FieldChange("storey", a.get("storey"), b.get("storey")))
    va, vb = _num(a.get("ceiling_void_mm")), _num(b.get("ceiling_void_mm"))
    if (va is None) != (vb is None) or (va is not None and vb is not None and abs(va - vb) > VOID_ABS_MM):
        out.append(FieldChange("ceiling_void_mm", a.get("ceiling_void_mm"), b.get("ceiling_void_mm")))
    if _norm(a.get("name")) != _norm(b.get("name")):
        out.append(FieldChange("name", a.get("name"), b.get("name")))
    return tuple(out)


def diff_spaces(old: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]],
                centroid_tol_m: float = CENTROID_TOL_M) -> list[SpaceDiff]:
    pairs, removed, added = match_spaces(old, new, centroid_tol_m)
    items = [SpaceDiff("changed" if (f := _space_fields(a, b)) else "unchanged", _label(b), a, b, f, how) for a, b, how in pairs]
    items += [SpaceDiff("removed", _label(a), a, None) for a in removed]
    items += [SpaceDiff("added", _label(b), None, b) for b in added]
    return sorted(items, key=lambda d: (d.change, d.key))


def diff_inputs(old: Mapping[str, Mapping[str, tuple[Any, str | None]]],
                new: Mapping[str, Mapping[str, tuple[Any, str | None]]]) -> list[InputDiff]:
    """Per system tag: {input name: (value, unit)} in the parent and in the child."""
    out: list[InputDiff] = []
    for tag in sorted(set(old) | set(new)):
        a, b = old.get(tag, {}), new.get(tag, {})
        for name in sorted(set(a) | set(b)):
            if name not in b:
                out.append(InputDiff("removed", tag, name, a[name][0], None, a[name][1], None))
            elif name not in a:
                out.append(InputDiff("added", tag, name, None, b[name][0], None, b[name][1]))
            elif not _same_value(a[name], b[name]):
                out.append(InputDiff("changed", tag, name, a[name][0], b[name][0], a[name][1], b[name][1]))
    return out


def _same_value(a: tuple[Any, str | None], b: tuple[Any, str | None]) -> bool:
    (va, ua), (vb, ub) = a, b
    if ua != ub:
        return False
    fa, fb = _num(va), _num(vb)
    if fa is not None and fb is not None:
        return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12)
    return bool(va == vb)


@dataclass(frozen=True)
class Cause:
    """One change that reaches a result, with the graph edge it travelled along."""

    change: Change
    subject: str | None                        # the system tag, or None for a project- or space-level change
    detail: str                                # plain description built from the two rows' values
    edge: Edge


@dataclass
class StaleResult:
    subject_id: str
    rule_id: str
    causes: list[Cause] = field(default_factory=list)


def changes_of(spaces: Iterable[SpaceDiff], inputs: Iterable[InputDiff]) -> list[tuple[Change, str | None, str]]:
    """(graph change, system tag or None, description) for every difference. Added and removed spaces change the building's space
    set as a whole, so they reach the same rules a field change would."""
    out: list[tuple[Change, str | None, str]] = []
    for d in spaces:
        if d.change == "unchanged":
            continue
        if d.change == "changed":
            for f in d.fields:
                out.append((Change("space", f.field), None, f"space {d.key}: {f.field} {f.old} -> {f.new}"))
        else:
            out.append((Change("space", "area_m2"), None, f"space {d.key} {d.change}"))
    for i in inputs:
        kind = Change("project", "building_part") if i.is_part_change else Change("input", i.name)
        text = f"system {i.system}: {i.name} {i.old} -> {i.new}" if i.change == "changed" else f"system {i.system}: {i.name} {i.change}"
        out.append((kind, i.system, text))
    return out


def stale_results(graph: DependencyGraph, changes: Sequence[tuple[Change, str | None, str]],
                  results: Iterable[Mapping[str, Any]]) -> list[StaleResult]:
    """Which of the parent's (subject, rule) results a set of changes reaches. A system-level change touches only that system's
    results; a project- or space-level change touches every subject's results of the affected rules."""
    by_key: dict[tuple[str, str], StaleResult] = {}
    for r in results:
        subject, rule = str(r["subject_id"]), str(r["rule_id"])
        for change, tag, detail in changes:
            if tag is not None and tag != subject:
                continue
            for edge in graph.affected(change):
                if edge.target == "rule:" + rule:
                    stale = by_key.setdefault((subject, rule), StaleResult(subject, rule))
                    if not any(c.change == change and c.subject == tag and c.edge == edge and c.detail == detail
                           for c in stale.causes):
                        stale.causes.append(Cause(change, tag, detail, edge))
    return [by_key[k] for k in sorted(by_key)]
