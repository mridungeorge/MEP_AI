"""The NSW design compliance declaration DRAFT (JSON and PDF). Read-only: it lodges nothing and states nothing on the practitioner's behalf."""
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from mep.api.review import Service as ReviewSvc
from mep.api.review import _run
from mep.api.revisions import User as RevUser
from mep.api.revisions import _err
from mep.api.services import Dsn, _as_user
from mep.review import declaration as decl

router = APIRouter()


def _pathways(dsn: str, user: Any, revision_id: UUID) -> list[dict[str, Any]]:
    with _as_user(dsn, user) as conn:
        paths = conn.execute("select subject_id, rule_id, pathway, note from result_pathway where revision_id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchall()
        ev = conn.execute("select subject_id, rule_id, title from perf_evidence where revision_id = %s and firm_id = %s order by created_at", (revision_id, user.firm_id)).fetchall()
    return [{"subject": p["subject_id"], "rule_id": p["rule_id"], "pathway": p["pathway"], "note": p["note"],
             "evidence": [{"title": e["title"]} for e in ev if (e["subject_id"], e["rule_id"]) == (p["subject_id"], p["rule_id"])]} for p in paths]


def _build(svc: Any, user: Any, dsn: str, revision_id: UUID) -> dict[str, Any]:
    package = _run(svc.package, revision_id)
    try:
        return decl.build(package, _pathways(dsn, user, revision_id))
    except decl.NotApplicable as exc:
        raise _err(409 if exc.code in ("not_signed", "ledger_unverified") else 422, exc.code, exc.message) from None


@router.get("/revisions/{revision_id}/nsw-declaration")
def declaration(revision_id: UUID, user: RevUser, svc: ReviewSvc, dsn: Dsn) -> dict[str, Any]:
    return _build(svc, user, dsn, revision_id)


@router.get("/revisions/{revision_id}/nsw-declaration.pdf")
def declaration_pdf(revision_id: UUID, user: RevUser, svc: ReviewSvc, dsn: Dsn) -> Response:
    d = _build(svc, user, dsn, revision_id)
    data = decl.to_pdf(d)
    verdict = decl.validate_pdf(data, d)
    if not verdict["passed"]:                                 # a draft that failed its own checks is never returned (non-negotiable 9)
        raise _err(500, "validator_failed", "the draft failed its own checks and is withheld: " + ", ".join(sorted(k for k, ok in verdict["checks"].items() if not ok)))
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="nsw-declaration-draft-{revision_id}.pdf"',
                                                                        "X-Validator": "passed", "Cache-Control": "no-store"})
