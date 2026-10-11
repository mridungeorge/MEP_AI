"""Commissioning sheets (XLSX and PDF) from a SIGNED revision, and the re-import of measured values with tolerance flags. Readings are records, not results."""
import hashlib
import json
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row

from mep.api.review import Service as ReviewSvc
from mep.api.review import _run
from mep.api.revisions import DESIGNER_ROLES, Repo, _err
from mep.api.revisions import User as RevUser
from mep.api.services import Dsn, _as_user, runs_of
from mep.review import commissioning as cx

router = APIRouter()


def _signed_rows(svc: Any, user: Any, dsn: str, revision_id: UUID) -> list[dict[str, Any]]:
    package = _run(svc.package, revision_id)
    if not package["status"]["complete"]:
        raise _err(409, "not_signed", "commissioning sheets come from a signed revision (missing: " + ", ".join(package["status"]["missing"]) + ")")
    if not package["ledger"]["verified"]:
        raise _err(409, "ledger_unverified", "the ledger does not verify")
    with _as_user(dsn, user) as conn:
        names = {str(r["id"]): r["name"] for r in conn.execute("select id, name from space where revision_id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchall()}
    terminals = [r for r in runs_of(dsn, user, revision_id) if r["kind"] == "terminal"]
    rows = cx.rows_of(terminals, names)
    if len(rows) > cx.MAX_ROWS:
        raise _err(422, "too_many_terminals", f"a sheet holds at most {cx.MAX_ROWS} terminals (this revision has {len(rows)}); split the work by system")
    clash = cx.collisions(rows)
    if clash:
        raise _err(409, "tag_collision", "terminal tags collide within a system, so a measured value could not be matched to one terminal: " + ", ".join(clash[:5]))
    if not rows:
        raise _err(422, "no_terminals", "this revision has no terminals with a design airflow in its services schedule")
    return rows


@router.get("/revisions/{revision_id}/commissioning.xlsx")
def sheet_xlsx(revision_id: UUID, user: RevUser, svc: ReviewSvc, dsn: Dsn) -> Response:
    data = cx.to_xlsx(_signed_rows(svc, user, dsn, revision_id), str(revision_id))
    return Response(content=data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="commissioning.xlsx"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/revisions/{revision_id}/commissioning.pdf")
def sheet_pdf(revision_id: UUID, user: RevUser, svc: ReviewSvc, dsn: Dsn) -> Response:
    return Response(content=cx.to_pdf(_signed_rows(svc, user, dsn, revision_id), str(revision_id)), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="commissioning.pdf"', "Cache-Control": "no-store"})


@router.post("/revisions/{revision_id}/commissioning/import")
def import_readings(revision_id: UUID, user: RevUser, svc: ReviewSvc, dsn: Dsn, tolerance_pct: Annotated[float, Form(ge=0, le=50)],
                    file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer imports site readings")
    rows = _signed_rows(svc, user, dsn, revision_id)
    data = file.file.read(cx.MAX_IMPORT_BYTES + 1)
    try:
        parsed = cx.parse_import(data, rows, tolerance_pct)
    except cx.ImportRefused as exc:
        raise _err(422, "bad_sheet", str(exc)) from None
    counts: dict[str, int] = {}
    for r in parsed:
        counts[r["flag"]] = counts.get(r["flag"], 0) + 1
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        batch = conn.execute("insert into commissioning_batch (firm_id, revision_id, file_sha256, tolerance_pct, counts, imported_by) values (%s, %s, %s, %s, %s::jsonb, %s) returning id",
                             (user.firm_id, revision_id, hashlib.sha256(data).hexdigest(), tolerance_pct, json.dumps(counts), user.user_id)).fetchone()
        assert batch is not None
        with conn.cursor() as cur:
            cur.executemany("insert into commissioning_reading (firm_id, batch_id, revision_id, terminal, system_tag, design_ls, measured_ls, variance_pct, tolerance_pct, flag, measured_on, measured_by, comment)"
                            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            [(user.firm_id, batch["id"], revision_id, r["terminal"][:80], r["system"][:80], r["design_ls"], r["measured_ls"], r["variance_pct"], tolerance_pct, r["flag"],
                              r["measured_on"], r["measured_by"], r["comment"]) for r in parsed])
    return {"batch_id": str(batch["id"]), "tolerance_pct": tolerance_pct, "counts": counts, "readings": parsed,
            "note": "Site readings are recorded as records. They are not rule results and no rule reads them."}


@router.get("/revisions/{revision_id}/commissioning/readings")
def readings(revision_id: UUID, user: RevUser, repo: Repo, dsn: Dsn) -> dict[str, Any]:
    if repo.revision_info(revision_id, user.firm_id) is None:
        raise _err(404, "not_found", "revision not found")
    with _as_user(dsn, user) as conn:
        batches = conn.execute("select id, tolerance_pct, counts, imported_at from commissioning_batch where revision_id = %s and firm_id = %s order by imported_at desc", (revision_id, user.firm_id)).fetchall()
        rows = conn.execute("select batch_id, terminal, system_tag, design_ls, measured_ls, variance_pct, flag from commissioning_reading where revision_id = %s and firm_id = %s order by terminal", (revision_id, user.firm_id)).fetchall()
    return {"batches": [{"id": str(b["id"]), "tolerance_pct": float(b["tolerance_pct"]), "counts": b["counts"], "imported_at": b["imported_at"].isoformat(),
                         "readings": [{k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in r.items() if k != "batch_id"} for r in rows if r["batch_id"] == b["id"]]} for b in batches]}
