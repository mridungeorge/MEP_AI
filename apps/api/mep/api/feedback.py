"""In-app feedback (members send, administrators read) and the public pricing read. No attachments, no personal data beyond the sender's own user id."""
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mep.api.billing import load_plans
from mep.api.revisions import User as RevUser
from mep.api.revisions import _err
from mep.api.services import Dsn, _as_user

router = APIRouter()
NO_CONTROL = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"


class FeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["bug", "idea", "question", "other"] = "other"
    page: Annotated[str, StringConstraints(max_length=200, pattern=NO_CONTROL)] | None = None
    message: str = Field(min_length=3, max_length=2000, pattern=r"(?s)^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$")


@router.post("/feedback")
def send(body: FeedbackBody, user: RevUser, dsn: Dsn) -> dict[str, Any]:
    if len(body.message.strip()) < 3:
        raise _err(422, "too_short", "write a few words")
    try:
        with _as_user(dsn, user) as conn:
            row = conn.execute("select submit_feedback(%s, %s, %s) as id", (body.kind, body.page, body.message)).fetchone()
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    except psycopg.errors.RaiseException as exc:
        raise _err(429, "too_much_feedback", str(exc).splitlines()[0]) from None
    assert row is not None
    return {"id": str(row["id"]), "thanks": True}


@router.get("/admin/feedback")
def listing(user: RevUser, dsn: Dsn) -> dict[str, Any]:
    with _as_user(dsn, user) as conn:
        if not conn.execute("select current_user_is_admin() as a").fetchone()["a"]:
            raise _err(403, "forbidden", "only an administrator of the firm reads the feedback")
        rows = conn.execute("select f.id, f.kind, f.page, f.message, f.created_at, a.email from feedback f left join app_user a on a.id = f.created_by and a.firm_id = f.firm_id"
                            " where f.firm_id = %s order by f.created_at desc limit 200", (user.firm_id,)).fetchall()
    return {"feedback": [{**r, "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/public/pricing")
def pricing() -> dict[str, Any]:
    """Public, read-only: the plans from the billing configuration, labelled as draft. No account needed; nothing here is a commercial offer."""
    plans = load_plans()
    return {"banner": "DRAFT COPY: prices are test-mode placeholders, not an offer", "currency": plans["currency"], "trial_days": plans["trial_days"],
            "plans": [{k: p[k] for k in ("id", "name", "seat_cents", "project_cents", "max_projects")} for p in plans["plans"]]}
