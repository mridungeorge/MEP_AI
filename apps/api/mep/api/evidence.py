"""The evidence table: what was READ from a drawing (a PDF through the vision model, or the other uploads) and is not yet anybody's input.

Everything listed here is provenance 'extracted'. A designer may turn a candidate into a space of their own (it then needs Gate 1 like any
other), but nothing here is ever read by the rule engine.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

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


@router.get("/revisions/{revision_id}/evidence")
def evidence(revision_id: UUID, user: User, dsn: Annotated[str, Depends(get_dsn)], source: str | None = None) -> dict[str, Any]:
    """Candidate spaces per source file, grouped from the evidence rows: {sources: [{name, kind, sha256, problems, candidates: [...]}]}."""
    with _as_user(dsn, user) as conn:
        if conn.execute("select 1 from revision where id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "revision not found"})
        runs = conn.execute("select id, source_kind, source_name, source_sha256, problems, metadata from ingest_run where revision_id = %s and firm_id = %s"
                            " and (%s::text is null or source_kind = %s) order by created_at", (revision_id, user.firm_id, source, source)).fetchall()
        rows = conn.execute("select ingest_run, entity_key, field, value_number, value_text, unit, confidence from extraction where revision_id = %s"
                            " and firm_id = %s and entity_kind = 'space' order by entity_key, field", (revision_id, user.firm_id)).fetchall()
    by_run: dict[Any, dict[str, dict[str, Any]]] = {}
    for r in rows:
        cand = by_run.setdefault(r["ingest_run"], {}).setdefault(r["entity_key"], {"key": r["entity_key"], "confidence": None})
        cand[r["field"]] = float(r["value_number"]) if r["value_number"] is not None else r["value_text"]
        if r["confidence"] is not None:
            cand["confidence"] = float(r["confidence"])
    return {"sources": [{"name": run["source_name"], "kind": run["source_kind"], "sha256": run["source_sha256"], "problems": run["problems"],
                         "metadata": run["metadata"], "provenance": "extracted",
                         "candidates": list(by_run.get(run["id"], {}).values())} for run in runs]}
