"""The projects dashboard: every project of the firm with its latest revision and where it stands, filterable; and a project's revision history with
who signed what. Read as the signed-in user, so row-level security decides what is listed.
"""
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.rows import dict_row

from mep.api.schedule import CurrentUser

router = APIRouter()
Status = Literal["drafting", "in_review", "signed"]


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="projects are not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Dsn = Annotated[str, Depends(get_dsn)]


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


def stage(gates: set[str], frozen: bool) -> str:
    """Where a revision stands: signed (Gate 3), in review (frozen or Gate 2 signed), or still being drafted."""
    if "gate3" in gates:
        return "signed"
    if frozen or "gate2" in gates:
        return "in_review"
    return "drafting"


@router.get("/projects")
def projects(user: User, dsn: Dsn, state: Annotated[str | None, Query(pattern="^(NSW|VIC|QLD|WA|SA|TAS|ACT|NT)$")] = None,
             edition: Annotated[str | None, Query(pattern="^NCC20[0-9]{2}$")] = None, status: Status | None = None,
             q: Annotated[str | None, Query(max_length=100)] = None) -> list[dict[str, Any]]:
    with _as_user(dsn, user) as conn:
        rows = conn.execute(
            "select p.id, p.address, p.state, p.ncc_edition, p.climate_zone, p.created_at from project p where p.firm_id = %s"
            " and (%s::text is null or p.state = %s) and (%s::text is null or p.ncc_edition = %s)"
            " and (%s::text is null or p.address ilike '%%' || %s || '%%') order by p.created_at desc",
            (user.firm_id, state, state, edition, edition, q, q)).fetchall()
        revs = conn.execute("select r.id, r.project_id, r.architect_rev, r.created_at, r.frozen_at is not null as frozen from revision r where r.firm_id = %s"
                            " order by r.created_at", (user.firm_id,)).fetchall()
        signs = conn.execute("select s.revision_id, s.gate::text as gate from signoff s where s.firm_id = %s", (user.firm_id,)).fetchall()
    gates: dict[Any, set[str]] = {}
    for s in signs:
        gates.setdefault(s["revision_id"], set()).add(s["gate"])
    by_project: dict[Any, list[dict[str, Any]]] = {}
    for r in revs:
        by_project.setdefault(r["project_id"], []).append(r)
    out = []
    for p in rows:
        rl = by_project.get(p["id"], [])
        latest = rl[-1] if rl else None
        st = stage(gates.get(latest["id"], set()), latest["frozen"]) if latest else "drafting"
        if status is not None and st != status:
            continue
        out.append({"id": str(p["id"]), "address": p["address"], "state": p["state"], "ncc_edition": p["ncc_edition"], "climate_zone": p["climate_zone"],
                    "created_at": p["created_at"].isoformat(), "revisions": len(rl), "status": st,
                    "latest_revision": None if latest is None else {"id": str(latest["id"]), "architect_rev": latest["architect_rev"], "frozen": latest["frozen"],
                                                                    "gates_signed": sorted(gates.get(latest["id"], set()))}})
    return out


@router.get("/projects/{project_id}/history")
def history(project_id: UUID, user: User, dsn: Dsn) -> dict[str, Any]:
    with _as_user(dsn, user) as conn:
        p = conn.execute("select id, address, state, ncc_edition, climate_zone from project where id = %s and firm_id = %s", (project_id, user.firm_id)).fetchone()
        if p is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no such project"})
        revs = conn.execute("select id, architect_rev, status, created_at, frozen_at, parent_revision_id from revision where project_id = %s and firm_id = %s"
                            " order by created_at", (project_id, user.firm_id)).fetchall()
        signs = conn.execute("select s.revision_id, s.gate::text as gate, s.signed_at, s.registration_no, u.email, s.signer_mode from signoff s"
                             " join app_user u on u.id = s.user_id where s.firm_id = %s and s.revision_id = any(%s) order by s.signed_at",
                             (user.firm_id, [r["id"] for r in revs])).fetchall()
        counts = conn.execute("select revision_id, result::text as outcome, count(*) as n from rule_result where firm_id = %s and current and revision_id = any(%s)"
                              " group by revision_id, result", (user.firm_id, [r["id"] for r in revs])).fetchall()
    sign_by: dict[Any, list[dict[str, Any]]] = {}
    for s in signs:
        sign_by.setdefault(s["revision_id"], []).append({"gate": s["gate"], "signed_at": s["signed_at"].isoformat(), "signer": s["email"],
                                                         "registration_no": s["registration_no"], "signer_mode": s["signer_mode"]})
    count_by: dict[Any, dict[str, int]] = {}
    for c in counts:
        count_by.setdefault(c["revision_id"], {})[c["outcome"]] = int(c["n"])
    return {"project": {**p, "id": str(p["id"])},
            "revisions": [{"id": str(r["id"]), "architect_rev": r["architect_rev"], "created_at": r["created_at"].isoformat(),
                           "frozen_at": None if r["frozen_at"] is None else r["frozen_at"].isoformat(),
                           "parent_revision_id": None if r["parent_revision_id"] is None else str(r["parent_revision_id"]),
                           "stage": stage({s["gate"] for s in sign_by.get(r["id"], [])}, r["frozen_at"] is not None),
                           "signoffs": sign_by.get(r["id"], []), "results": count_by.get(r["id"], {})} for r in revs]}
