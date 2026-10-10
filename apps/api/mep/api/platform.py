"""Platform administration: the operator verifies the registration numbers firms submit for their approvers.

A platform administrator is a person in `platform_admin` (set by the operator in the database, never through the app). They cannot decide a registration
they submitted or that is their own, and every decision is a ledger entry with what they checked.
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
    raise HTTPException(status_code=503, detail="platform administration is not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Dsn = Annotated[str, Depends(get_dsn)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


@router.get("/platform/registrations")
def pending(user: User, dsn: Dsn) -> list[dict[str, Any]]:
    try:
        with _as_user(dsn, user) as conn:
            rows = conn.execute("select * from platform_pending_registrations()").fetchall()
    except psycopg.errors.InsufficientPrivilege:
        raise _err(403, "forbidden", "platform administrators only") from None
    return [{**r, "id": str(r["id"]), "submitted_at": r["submitted_at"].isoformat()} for r in rows]


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verify: bool
    note: str = Field(min_length=3, max_length=1000, description="what was checked on the register, where and when (a rejection says why)")


@router.post("/platform/registrations/{registration_id}")
def decide(registration_id: UUID, body: Decision, user: User, dsn: Dsn) -> dict[str, Any]:
    try:
        with _as_user(dsn, user) as conn:
            conn.execute("select registration_decide(%s, %s, %s)", (registration_id, body.verify, body.note))
    except psycopg.errors.InsufficientPrivilege:
        raise _err(403, "forbidden", "platform administrators only") from None
    except psycopg.errors.RaiseException as exc:
        raise _err(422, "refused", str(exc).splitlines()[0]) from None
    return {"decided": True, "verified": body.verify}
