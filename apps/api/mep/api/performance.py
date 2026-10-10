"""The Performance Solution pathway: flag, choose, record evidence, export a starting data package.

* A FAILED result for which the engine finds no option that passes every dependent rule is flagged "Performance Solution pathway likely". The flag is the
  engine's finding, not a conclusion about the design; the designer records which pathway they pursue per result (DTS or Performance Solution).
* The engineer's simulation or assessment results are recorded as EVIDENCE (provenance `engineer_supplied`): never rule results, never inputs.
* The starting data package (JSON and XLSX) gathers what an engineer needs to open a Performance Solution: project facts, spaces, systems with their inputs, the
  failed DTS benchmarks (the rule's encoded thresholds and check, its clause reference, the inputs used), pathways and evidence. It carries the draft-rules banner and
  decides nothing.
"""
import hashlib
import io
import json
import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from mep.api.fixes import options_for
from mep.api.revisions import DESIGNER_ROLES, Graph, Pack, Repo, _err
from mep.api.revisions import User as RevUser
from mep.clash import clean as clash_clean
from mep.engine import units

router = APIRouter()
MAX_FILE = 10 * 1024 * 1024
BANNER = "STARTING DATA PACKAGE FOR A PERFORMANCE SOLUTION. DRAFT RULES: NOT ENGINEER-APPROVED. Nothing here is a compliance finding."


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="performance solutions are not configured")


Dsn = Annotated[str, Depends(get_dsn)]


@contextmanager
def _as_user(dsn: str, user: Any) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


def _view(dsn: str, user: Any, revision_id: UUID) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with _as_user(dsn, user) as conn:
        paths = conn.execute("select subject_id, rule_id, pathway, note, set_at from result_pathway where revision_id = %s and firm_id = %s",
                             (revision_id, user.firm_id)).fetchall()
        ev = conn.execute("select id, subject_id, rule_id, title, tool, description, metrics, provenance, file_name, file_sha256, created_at from perf_evidence"
                          " where revision_id = %s and firm_id = %s order by created_at", (revision_id, user.firm_id)).fetchall()
    return paths, [{**e, "id": str(e["id"]), "created_at": e["created_at"].isoformat()} for e in ev]


def _likely(repo: Any, pack: Any, graph: Any, revision_id: UUID, firm_id: UUID, subject: str, rule: str) -> bool:
    """True only when the rule FAILS on today's inputs and no single engineer-decided change makes it pass without another rule still failing or getting worse.
    A stored FAIL that the live inputs no longer reproduce is stale (re-run), not a pathway finding."""
    result, options, _ = options_for(repo, pack, graph, revision_id, firm_id, subject, rule)
    if result.get("live_outcome") != "FAIL":
        return False
    return not any(o["accepted"] for o in options)


@router.get("/revisions/{revision_id}/performance")
def overview(revision_id: UUID, user: RevUser, repo: Repo, pack: Pack, graph: Graph, dsn: Dsn) -> dict[str, Any]:
    if repo.revision_info(revision_id, user.firm_id) is None:
        raise _err(404, "not_found", "revision not found")
    paths, evidence = _view(dsn, user, revision_id)
    chosen = {(p["subject_id"], p["rule_id"]): p for p in paths}
    rows = []
    for r in repo.current_results(revision_id, user.firm_id):
        if r["outcome"] != "FAIL":
            continue
        key = (r["subject_id"], r["rule_id"])
        likely = _likely(repo, pack, graph, revision_id, user.firm_id, *key)
        p = chosen.get(key)
        rows.append({"subject_id": key[0], "rule_id": key[1], "clause": (r.get("citation") or {}).get("clause"), "performance_solution_likely": likely,
                     "flag": "Performance Solution pathway likely" if likely else None, "pathway": p["pathway"] if p else "DTS", "note": p["note"] if p else None,
                     "evidence": [e for e in evidence if (e["subject_id"], e["rule_id"]) == key]})
    return {"banner": BANNER, "results": rows}


class PathwayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pathway: Literal["DTS", "PERFORMANCE_SOLUTION"]
    note: str | None = Field(default=None, max_length=1000)


def _designer(user: Any) -> None:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer records the pathway and evidence")


def _refusal(exc: Exception) -> HTTPException:
    if isinstance(exc, psycopg.errors.InsufficientPrivilege):
        return _err(403, "forbidden", str(exc).splitlines()[0])
    if isinstance(exc, psycopg.errors.RaiseException) and "frozen" in str(exc):
        return _err(409, "revision_frozen", "the revision is frozen")
    return _err(422, "refused", str(exc).splitlines()[0])


@router.put("/revisions/{revision_id}/performance/{subject_id}/{rule_id}/pathway")
def set_pathway(revision_id: UUID, subject_id: str, rule_id: str, body: PathwayBody, user: RevUser, dsn: Dsn) -> dict[str, Any]:
    _designer(user)
    try:
        with _as_user(dsn, user) as conn:
            conn.execute("select perf_set_pathway(%s, %s, %s, %s, %s)", (revision_id, subject_id, rule_id, body.pathway, body.note))
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException) as exc:
        raise _refusal(exc) from None
    return {"saved": True, "pathway": body.pathway}


def parse_metrics(raw: str) -> list[dict[str, Any]]:
    try:
        items = json.loads(raw or "[]")
        assert isinstance(items, list) and len(items) <= 50
        out = []
        for m in items:
            assert isinstance(m, dict) and set(m) <= {"name", "value", "unit"}
            name, value, unit = str(m["name"]).strip(), m["value"], str(m["unit"]).strip()
            assert 1 <= len(name) <= 80 and isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
            assert units.valid_unit_text(unit)                    # every number carries a unit pint understands
            units.unit_of(unit)
            out.append({"name": name, "value": value, "unit": unit})
        return out
    except (ValueError, AssertionError, KeyError, TypeError, units.UnitError):
        raise _err(422, "bad_metrics", 'metrics are a JSON list like [{"name": "peak temperature", "value": 26.4, "unit": "degC"}], each with a name, a finite number and a unit') from None


@router.post("/revisions/{revision_id}/performance/{subject_id}/{rule_id}/evidence")
async def add_evidence(revision_id: UUID, subject_id: str, rule_id: str, user: RevUser, dsn: Dsn, title: Annotated[str, Form(max_length=200)],
                       tool: Annotated[str | None, Form(max_length=120)] = None, description: Annotated[str | None, Form(max_length=4000)] = None,
                       metrics: Annotated[str, Form(max_length=20000)] = "[]", file: Annotated[UploadFile | None, File()] = None) -> dict[str, Any]:
    _designer(user)
    parsed = parse_metrics(metrics)
    data = b""
    name = None
    if file is not None and file.filename:
        data = await file.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise _err(413, "too_large", "an evidence file is at most 10 MiB")
        name = file.filename[:200]
    try:
        with _as_user(dsn, user) as conn:
            row = conn.execute("select perf_add_evidence(%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s) as id",
                               (revision_id, subject_id, rule_id, title, tool, description, json.dumps(parsed), name, data or None,
                                hashlib.sha256(data).hexdigest() if data else None)).fetchone()
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.RaiseException, psycopg.errors.CheckViolation) as exc:
        raise _refusal(exc) from None
    return {"evidence_id": str(row["id"]), "provenance": "engineer_supplied", "note": "Recorded as evidence. It is not a rule result and no rule reads it."}   # type: ignore[index]


@router.get("/revisions/{revision_id}/performance/evidence/{evidence_id}/file")
def evidence_file(revision_id: UUID, evidence_id: UUID, user: RevUser, dsn: Dsn) -> Response:
    with _as_user(dsn, user) as conn:
        row = conn.execute("select * from perf_evidence_file(%s)", (evidence_id,)).fetchone()
    if row is None:
        raise _err(404, "not_found", "no such file")
    return Response(content=bytes(row["file_content"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="evidence"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# ---------------------------------------------------------------------------------------------------------------------------- the package
def build_package(repo: Any, pack: Any, graph: Any, dsn: Dsn, user: Any, revision_id: UUID) -> dict[str, Any]:
    info = repo.revision_info(revision_id, user.firm_id)
    if info is None:
        raise _err(404, "not_found", "revision not found")
    data = repo.load_run_inputs(revision_id, user.firm_id)
    paths, evidence = _view(dsn, user, revision_id)
    chosen = {(p["subject_id"], p["rule_id"]): p for p in paths}
    failed = []
    for r in repo.current_results(revision_id, user.firm_id):
        if r["outcome"] != "FAIL":
            continue
        rule = pack.rules.get(r["rule_id"])
        key = (r["subject_id"], r["rule_id"])
        failed.append({"subject_id": key[0], "rule_id": key[1], "outcome": "FAIL", "citation": r.get("citation"),
                       "rule_status": (r.get("citation") or {}).get("rule_status"),
                       "inputs_used": r.get("inputs_used"), "dts_check": None if rule is None else rule.raw.get("check"),
                       "dts_thresholds": None if rule is None else rule.thresholds, "causes": r.get("causes"),
                       "performance_solution_likely": _likely(repo, pack, graph, revision_id, user.firm_id, *key),
                       "pathway": chosen[key]["pathway"] if key in chosen else "DTS", "pathway_note": chosen[key]["note"] if key in chosen else None})
    project = {k: v for k, v in data["project"].items() if k in ("state", "ncc_edition", "climate_zone", "building_class", "approval_date")}
    return {"banner": BANNER, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "revision": {"id": str(revision_id), "architect_rev": info["architect_rev"],
            "frozen": info["frozen"]}, "project": project, "spaces": data.get("spaces", []),
            "systems": [{"tag": s["id"], "part": s.get("part"), "rules": s.get("rules", []), "inputs": [
                {"name": i["name"], "value": i["value"], "unit": i.get("unit"), "provenance": i["provenance"], "confirmed": i.get("confirmed_by") is not None}
                for i in s.get("inputs", [])]} for s in data.get("systems", [])],
            "failed_dts_benchmarks": failed, "evidence": evidence}


@router.get("/revisions/{revision_id}/performance/package.json")
def package_json(revision_id: UUID, user: RevUser, repo: Repo, pack: Pack, graph: Graph, dsn: Dsn) -> Response:
    body = json.dumps(build_package(repo, pack, graph, dsn, user, revision_id), indent=1, default=str)
    return Response(content=body, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="performance-solution-start.json"', "Cache-Control": "no-store"})


def safe_cell(v: Any) -> Any:
    """A spreadsheet cell: text that starts like a formula is stored as text, so opening the file never runs anything."""
    if isinstance(v, str):
        v = clash_clean(v) or ""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    if isinstance(v, dict | list):
        return json.dumps(v, default=str)
    return v


@router.get("/revisions/{revision_id}/performance/package.xlsx")
def package_xlsx(revision_id: UUID, user: RevUser, repo: Repo, pack: Pack, graph: Graph, dsn: Dsn) -> Response:
    from openpyxl import Workbook
    pkg = build_package(repo, pack, graph, dsn, user, revision_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in ([pkg["banner"]], ["Generated", pkg["generated_at"]], ["Revision", pkg["revision"]["architect_rev"]], ["Rules are DRAFT: not engineer-approved."]):
        ws.append([safe_cell(c) for c in row])

    def sheet(title: str, header: list[str], rows: list[list[Any]]) -> None:
        w = wb.create_sheet(title)
        w.append(header)
        for r in rows:
            w.append([safe_cell(c) for c in r])

    sheet("Project", ["field", "value"], [[k, v] for k, v in pkg["project"].items()])
    sheet("Spaces", ["name", "use", "storey", "area_m2", "ceiling_void_mm", "provenance"],
          [[s.get("name"), s.get("use"), s.get("storey"), s.get("area_m2"), s.get("ceiling_void_mm"), s.get("provenance")] for s in pkg["spaces"]])
    sheet("Systems", ["tag", "input", "value", "unit", "provenance", "confirmed"],
          [[s["tag"], i["name"], i["value"], i["unit"], i["provenance"], i["confirmed"]] for s in pkg["systems"] for i in s["inputs"]])
    sheet("Failed DTS benchmarks", ["system", "rule", "clause", "check", "thresholds", "inputs used", "Performance Solution likely", "pathway"],
          [[b["subject_id"], b["rule_id"], (b.get("citation") or {}).get("clause"), b["dts_check"], b["dts_thresholds"], b["inputs_used"], b["performance_solution_likely"], b["pathway"]]
           for b in pkg["failed_dts_benchmarks"]])
    sheet("Evidence", ["system", "rule", "title", "tool", "metric", "value", "unit", "file sha256"],
          [[e["subject_id"], e["rule_id"], e["title"], e["tool"], m["name"], m["value"], m["unit"], e["file_sha256"]] for e in pkg["evidence"] for m in (e["metrics"] or [{"name": None, "value": None, "unit": None}])])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(content=buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="performance-solution-start.xlsx"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
