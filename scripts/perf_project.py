"""Synthetic project generator and timing helpers for the performance checks (Phase 10.5). No web stack, no database.

Builds a project of N spaces and M systems with engineer-confirmed inputs for every rule the project's edition and state
select, then times the pure parts: the engine run, the revision diff plus cross-rule rerun, the package PDF, duct sizing
and clash-lite. Run directly for a printed table:  python scripts/perf_project.py
"""
import random
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from mep import clash, sizing
from mep.diff.graph import build_graph
from mep.diff.service import build_diff
from mep.engine.cross_rule import cross_rule_rerun
from mep.engine.loader import Rule, RulePack, load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import PROJECT_FACT_INPUTS, run, select_rules

SYSTEM_TYPES = ("air_conditioning", "air_conditioning_heating", "mechanical_ventilation", "exhaust")
USES = ("office", "meeting room", "store", "corridor", "plant")


def load() -> RulePack:
    return load_pack(ROOT / "rules")


def project_facts() -> ProjectFacts:
    return ProjectFacts(state="VIC", ncc_edition="NCC2025", climate_zone=6, building_class="5", approval_date=date(2026, 10, 6))


def _input(spec_type: str, unit: str | None, values: tuple[str, ...] | None, name: str, system_type: str, rng: random.Random) -> InputValue:
    conf = Provenance.ENGINEER_CONFIRMED
    if name == "system_type":
        return InputValue(system_type, None, conf)
    if spec_type == "bool":
        return InputValue(rng.random() < 0.3, None, conf)
    if spec_type == "enum" and values:
        return InputValue(values[rng.randrange(len(values))], None, conf)
    if spec_type == "int":
        return InputValue(rng.randrange(1, 10), unit, conf)
    if spec_type == "string":
        return InputValue("x", None, conf)
    return InputValue(round(rng.uniform(0.5, 3000.0), 2), unit, conf)


def make_subject(tag: str, system_type: str, rules: dict[str, Rule], seed: int) -> Subject:
    rng = random.Random(f"{seed}:{tag}")
    inputs: dict[str, InputValue] = {}
    for rule in rules.values():
        for name, spec in rule.inputs.items():
            if name not in inputs and name not in PROJECT_FACT_INPUTS:
                inputs[name] = _input(spec.type, spec.unit, spec.values, name, system_type, rng)
    return Subject(tag, sorted(rules), inputs)


def make_spaces(n: int, seed: int = 1) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [{"id": f"sp{i}", "ifc_guid": f"G{i:05d}", "name": f"Room {i}", "area_m2": round(rng.uniform(8, 120), 1),
             "use": USES[i % len(USES)], "storey": f"L{i // 20}", "ceiling_void_mm": 300.0 + (i % 4) * 50,
             "centroid_x_m": float(i % 20) * 6, "centroid_y_m": float(i // 20) * 6, "confirmed": True} for i in range(n)]


def make_request(pack: RulePack, n_systems: int = 20, seed: int = 1) -> RunRequest:
    rules = select_rules(pack, "NCC2025", "VIC")
    subjects = [make_subject(f"SYS-{i + 1:02d}", SYSTEM_TYPES[i % len(SYSTEM_TYPES)], rules, seed) for i in range(n_systems)]
    return RunRequest(project_facts(), subjects)


def revise(request: RunRequest, spaces: list[dict[str, Any]]) -> tuple[RunRequest, list[dict[str, Any]]]:
    """A second revision: every 7th space changes area, every 25th is removed, 5 are added; every 3rd system changes one airflow."""
    new_spaces = [{**s, "area_m2": s["area_m2"] * 1.2} if i % 7 == 0 else dict(s) for i, s in enumerate(spaces) if i % 25 != 0]
    new_spaces += [{**spaces[0], "id": f"new{k}", "ifc_guid": f"N{k}", "name": f"New {k}"} for k in range(5)]
    subjects = []
    for i, s in enumerate(request.subjects):
        inputs = dict(s.inputs)
        if i % 3 == 0 and "max_airside_component_airflow" in inputs:
            inputs["max_airside_component_airflow"] = InputValue(5000.0, "L/s", Provenance.ENGINEER_CONFIRMED)
        subjects.append(Subject(s.id, list(s.rules), inputs))
    return RunRequest(request.project, subjects), new_spaces


def _inputs_view(request: RunRequest) -> dict[str, dict[str, tuple[Any, str | None]]]:
    return {s.id: {k: (v.value, v.unit) for k, v in s.inputs.items()} for s in request.subjects}


def diff_and_rerun(pack: RulePack, old: RunRequest, new: RunRequest, old_spaces: list[dict[str, Any]],
                   new_spaces: list[dict[str, Any]], old_report: dict[str, Any], new_report: dict[str, Any]) -> tuple[dict[str, Any], int]:
    graph = build_graph(pack)
    data = {"parent": "rev-1", "parent_spaces": old_spaces, "child_spaces": new_spaces, "parent_inputs": _inputs_view(old),
            "child_inputs": _inputs_view(new), "parent_results": old_report["results"], "child_results": new_report["results"]}
    diff = build_diff(data, graph)
    moved = 0
    for s_old, s_new in zip(old.subjects, new.subjects, strict=True):
        changes = {k: v for k, v in s_new.inputs.items() if s_old.inputs[k] != v}
        if changes:
            moved += len(cross_rule_rerun(subject=s_old, project=old.project, changes=changes, pack=pack, graph=graph).moves)
    return diff, moved


def make_package(n_lines: int = 400) -> dict[str, Any]:
    """A package dict shaped like review.package.assemble's, with synthetic result lines."""
    cite = {"document": "NCC 2025 Volume One", "clause": "J6D3", "rule_status": "draft"}
    lines = [{"subject": f"SYS-{i % 20 + 1:02d}", "rule_id": f"NCC2025-J6D{i % 10}-rule-{i}", "part": None, "outcome": ("PASS", "FAIL", "NEEDS_JUDGEMENT")[i % 3],
              "citation": cite, "review_class": "routine", "reasons": ["unit mismatch on one input"] if i % 9 == 0 else [], "stale": False,
              "decision": "approve" if i % 2 else None, "reason": "checked against the drawing set", "bulk": False, "spot_check": False,
              "accepted_fail": None, "reviewed_by": "reviewer@example.com", "reviewed_at": None, "fix_hypotheses": []} for i in range(n_lines)]
    return {"package_version": 2, "independence_notice": None, "banner": "DRAFT COPY",
            "revision": {"id": "rev-2", "architect_rev": "B", "status": "frozen", "frozen_at": "2026-10-06", "derived_from": ["A"]},
            "project": {"address": "1 Test St", "state": "VIC", "climate_zone": 6, "ncc_edition": "NCC2025", "approval_date": "2026-10-06",
                        "building_parts": [{"class": "5", "storeys": 6, "area_m2": 12000.0}]},
            "summary": {"PASS": n_lines // 3, "FAIL": n_lines // 3, "NEEDS_JUDGEMENT": n_lines // 3}, "accepted_fails": [], "results": lines,
            "signoffs": [], "status": {"signed_gates": [], "complete": False, "missing": ["gate1", "gate2", "gate3"]},
            "ledger": {"verified": True, "events": 10, "broken_at": None, "reason": None, "head_seq": 10, "head_hash": "ab" * 32,
                       "anchor_seq": 10, "anchor_hash": "ab" * 32}}


def make_duct_runs(n: int, seed: int = 1) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    return [{"id": i, "tag": f"D{i}", "shape": "rect" if i % 2 else "round", "width_mm": 400.0, "depth_mm": 250.0, "diameter_mm": 300.0,
             "insulation_mm": 25.0, "x0": rng.uniform(0, 100000), "y0": rng.uniform(0, 100000), "z0": 3000.0,
             "x1": rng.uniform(0, 100000), "y1": rng.uniform(0, 100000), "z1": 3000.0, "airflow_ls": rng.uniform(50, 3000)} for i in range(n)]


def make_boxes(n: int, seed: int = 2, extent: float = 100000.0) -> list[clash.Box]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        x, y, z = rng.uniform(0, extent), rng.uniform(0, extent), rng.uniform(0, 6000)
        out.append(clash.Box(f"g{i}", "IfcBeam", f"b{i}", (x, y, z), (x + 300, y + 300, z + 300)))
    return out


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    t = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t


def main() -> None:
    from mep.review.package import to_pdf

    pack = load()
    spaces = make_spaces(200)
    req = make_request(pack)
    rep, t_run = timed(lambda: run(req, pack))
    req2, spaces2 = revise(req, spaces)
    rep2 = run(req2, pack)
    (_, moved), t_diff = timed(lambda: diff_and_rerun(pack, req, req2, spaces, spaces2, rep, rep2))
    pdf, t_pdf = timed(lambda: to_pdf(make_package(400)))
    settings = sizing.clean_settings(None)
    _, t_size = timed(lambda: [sizing.size_duct(r["airflow_ls"], "rect" if r["id"] % 2 else "round", settings) for r in make_duct_runs(2000)])
    ducts, boxes = make_duct_runs(500), make_boxes(20000)
    found, t_clash = timed(lambda: clash.detect(ducts, [{"discipline": "structure", "file_name": "s.ifc", "elements": boxes}], 50.0, limit=2000))
    print(f"results {len(rep['results'])} | engine run {t_run:.2f}s | diff+rerun {t_diff:.2f}s ({moved} moves) | package pdf {t_pdf:.2f}s "
          f"({len(pdf)} bytes) | sizing 2000 runs {t_size:.2f}s | clash {t_clash:.2f}s ({len(found)} found)")


if __name__ == "__main__":
    main()
