"""The evidence table: what was READ from a drawing (a PDF through the vision model, or the other uploads) and is not yet anybody's input.

Everything listed here is provenance 'extracted'. A designer may turn a candidate into a space: it stays EXTRACTED, keeps a link to the evidence row
and the page, records the unit the designer declared (none is assumed) and needs Gate 1 like any other extracted value. Nothing here is read by the
rule engine until then.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from mep.api.schedule import CurrentUser

router = APIRouter()


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="evidence is not configured")


User = Annotated[CurrentUser, Depends(current_user)]


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


class AddSpaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entity_key: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    area_unit: str = Field(min_length=1, max_length=10)
    void_unit: str | None = Field(default=None, max_length=10)


@router.post("/revisions/{revision_id}/evidence/spaces")
def add_space(revision_id: UUID, body: AddSpaceBody, user: User, dsn: Annotated[str, Depends(get_dsn)]) -> dict[str, Any]:
    """Make a space from one evidence candidate. The unit must be stated; the space is `extracted` and linked to its evidence and page."""
    try:
        with _as_user(dsn, user) as conn:
            sid = conn.execute("select evidence_add_space(%s, %s, %s, %s, %s) as id",
                               (revision_id, body.source_sha256, body.entity_key, body.area_unit, body.void_unit)).fetchone()["id"]
    except psycopg.errors.InsufficientPrivilege as exc:
        raise HTTPException(status_code=403, detail={"code": "forbidden", "message": str(exc).splitlines()[0]}) from None
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail={"code": "already_added", "message": "this candidate is already a space of this revision"}) from None
    except psycopg.errors.RaiseException as exc:
        msg = str(exc).splitlines()[0]
        raise HTTPException(status_code=409 if "frozen" in msg else 422, detail={"code": "evidence_refused", "message": msg}) from None
    return {"space_id": str(sid), "provenance": "extracted", "needs": "Gate 1 confirmation"}


@router.get("/revisions/{revision_id}/evidence")
def evidence(revision_id: UUID, user: User, dsn: Annotated[str, Depends(get_dsn)], source: str | None = None) -> dict[str, Any]:
    """Candidate spaces per source file, grouped from the evidence rows: {sources: [{name, kind, sha256, problems, candidates: [...]}]}."""
    with _as_user(dsn, user) as conn:
        if conn.execute("select 1 from revision where id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "revision not found"})
        runs = conn.execute("select id, source_kind, source_name, source_sha256, problems, metadata from ingest_run where revision_id = %s and firm_id = %s"
                            " and (%s::text is null or source_kind = %s) order by created_at", (revision_id, user.firm_id, source, source)).fetchall()
        rows = conn.execute("select id, ingest_run, entity_key, field, value_number, value_text, unit, confidence from extraction where revision_id = %s"
                            " and firm_id = %s and entity_kind = 'space' order by entity_key, field", (revision_id, user.firm_id)).fetchall()
    by_run: dict[Any, dict[str, dict[str, Any]]] = {}
    for r in rows:
        cand = by_run.setdefault(r["ingest_run"], {}).setdefault(r["entity_key"], {"key": r["entity_key"], "confidence": None})
        cand[r["field"]] = float(r["value_number"]) if r["value_number"] is not None else r["value_text"]
        if r["confidence"] is not None:
            cand["confidence"] = float(r["confidence"])
        if r["unit"] == "unverified":
            cand[r["field"] + "_unit"] = "unverified"        # the number was read from a drawing with no unit assumed
    return {"sources": [{"name": run["source_name"], "kind": run["source_kind"], "sha256": run["source_sha256"], "problems": run["problems"],
                         "metadata": run["metadata"], "provenance": "extracted",
                         "candidates": list(by_run.get(run["id"], {}).values())} for run in runs]}
