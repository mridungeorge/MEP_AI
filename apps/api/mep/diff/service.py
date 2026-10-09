"""The revision diff as the API serves it: pure, built from the rows of a revision and its parent.

`build_diff(data, graph)` takes what the repository read (spaces, system inputs and current results of the child and of its parent)
and returns the differences, the parent's results they make stale, the reasoning trace of each, and a HASH of the differences.
The designer's confirmation names that hash; a run is refused when the live diff no longer hashes to it.
"""
import hashlib
import json
from collections.abc import Mapping
from typing import Any

from mep.diff.graph import DependencyGraph
from mep.diff.revision import SpaceDiff, changes_of, diff_inputs, diff_spaces, stale_results
from mep.diff.trace import trace_for, verify_trace

_SPACE_FIELDS = ("ifc_guid", "name", "area_m2", "use", "storey", "ceiling_void_mm")


def _space_view(d: SpaceDiff) -> dict[str, Any]:
    row = d.new or d.old or {}
    return {"change": d.change, "key": d.key, "matched_by": d.matched_by,
            "fields": [{"field": f.field, "old": f.old, "new": f.new} for f in d.fields],
            "space_id": None if d.new is None else d.new.get("id"),
            "confirmed": None if d.new is None else bool(d.new.get("confirmed")),
            "old": None if d.old is None else {k: d.old.get(k) for k in _SPACE_FIELDS},
            "new": None if d.new is None else {k: d.new.get(k) for k in _SPACE_FIELDS},
            "name": row.get("name")}


def _hash(spaces: list[dict[str, Any]], inputs: list[dict[str, Any]]) -> str:
    canonical = {"spaces": [{k: s[k] for k in ("change", "key", "fields", "old", "new")} for s in spaces],
                 "inputs": inputs}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def build_diff(data: Mapping[str, Any], graph: DependencyGraph) -> dict[str, Any]:
    parent = data.get("parent")
    if parent is None:
        return {"parent": None, "spaces": [], "inputs": [], "stale": [], "traces": [], "hash": None, "confirmed": False,
                "needs_confirmation": [], "has_changes": False}
    space_items = [d for d in diff_spaces(data["parent_spaces"], data["child_spaces"]) if d.change != "unchanged"]
    input_items = diff_inputs(data["parent_inputs"], data["child_inputs"])
    spaces = [_space_view(d) for d in space_items]
    inputs = [{"change": i.change, "system": i.system, "name": i.name, "old": i.old, "new": i.new,
               "unit_old": i.unit_old, "unit_new": i.unit_new, "is_part_change": i.is_part_change} for i in input_items]
    stale = stale_results(graph, changes_of(space_items, input_items), data.get("parent_results", []))
    before = {(r["subject_id"], r["rule_id"]): r for r in data.get("parent_results", [])}
    after = {(r["subject_id"], r["rule_id"]): r for r in data.get("child_results", [])}
    traces = []
    for s in stale:
        t = trace_for(s, before.get((s.subject_id, s.rule_id)), after.get((s.subject_id, s.rule_id)))
        if verify_trace(t, graph):                   # a trace the graph does not support is a bug, never shown
            raise RuntimeError(f"reasoning trace for {s.subject_id}/{s.rule_id} is not supported by the dependency graph")
        traces.append({**t.as_dict(), "lines": t.lines(), "before": (before.get((s.subject_id, s.rule_id)) or {}).get("outcome"),
                       "after": (after.get((s.subject_id, s.rule_id)) or {}).get("outcome")})
    digest = _hash(spaces, inputs)
    return {"parent": parent, "spaces": spaces, "inputs": inputs, "traces": traces,
            "stale": [{"subject_id": s.subject_id, "rule_id": s.rule_id} for s in stale],
            "hash": digest, "confirmed": data.get("confirmed_hash") == digest,
            "needs_confirmation": [s["space_id"] for s in spaces if s["change"] in ("changed", "added") and not s["confirmed"]],
            "has_changes": bool(spaces or inputs)}
