"""Firm administration: people, roles, invitations, firm settings and the firm's drafting templates.

Everything here is done AS the signed-in user; the database functions check that the caller is an active administrator of the same firm and
write the ledger. Nothing in this module can set a registration number (see platform.py) or touch another firm.
"""
import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from mep.api.schedule import CurrentUser

router = APIRouter()
MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
LAYER_NAME = re.compile(r"^[A-Za-z0-9_$\-]{1,64}$")


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def token_subject() -> Any:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="administration is not configured")


def get_mailer() -> Any:
    return None


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


def _call(dsn: str, user: CurrentUser, sql: str, args: tuple[Any, ...]) -> Any:
    """Run one admin function as the user and map the database's refusals to HTTP."""
    try:
        with _as_user(dsn, user) as conn:
            row = conn.execute(sql, args).fetchone()
        return None if row is None else next(iter(row.values()))
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    except psycopg.errors.UniqueViolation:
        raise _err(409, "already_exists", "that already exists") from None
    except psycopg.errors.RaiseException as exc:
        raise _err(422, "refused", str(exc).splitlines()[0]) from None
    except psycopg.errors.CheckViolation as exc:
        raise _err(422, "invalid", str(exc).splitlines()[0]) from None


def _require_admin(dsn: str, user: CurrentUser) -> None:
    with _as_user(dsn, user) as conn:
        row = conn.execute("select current_user_is_admin() as a").fetchone()
    if not row or not row["a"]:
        raise _err(403, "forbidden", "only an administrator of the firm may do this")


# ------------------------------------------------------------------------------------------------------------------------ overview
@router.get("/admin/overview")
def overview(user: User, dsn: Dsn) -> dict[str, Any]:
    _require_admin(dsn, user)
    with _as_user(dsn, user) as conn:
        firm = conn.execute("select id, name, signer_mode, sample_size, near_miss_default, void_clearance_mm from firm where id = %s", (user.firm_id,)).fetchone()
        users = conn.execute("select id, email, role::text as role, also_roles::text[] as also_roles, is_admin, active, registration_no from app_user"
                             " where firm_id = %s order by email nulls last, id", (user.firm_id,)).fetchall()
        invites = conn.execute("select id, email, role::text as role, status, expires_at, created_at from invitation where firm_id = %s"
                               " and status = 'pending' order by created_at desc", (user.firm_id,)).fetchall()
        templates = conn.execute("select id, kind, name, media_type, sha256, created_at from firm_template where firm_id = %s"
                                 " order by kind, created_at desc", (user.firm_id,)).fetchall()
        regs = conn.execute("select r.id, r.user_id, u.email, r.number, r.register, r.state_scheme, r.status, r.submitted_at, r.decided_at, r.decision_note"
                            " from registration r join app_user u on u.id = r.user_id where r.firm_id = %s order by r.submitted_at desc limit 100",
                            (user.firm_id,)).fetchall()
    return {
        "firm": {**firm, "id": str(firm["id"]), "near_miss_default": None if firm["near_miss_default"] is None else float(firm["near_miss_default"]),
                 "void_clearance_mm": float(firm["void_clearance_mm"])},
        "users": [{**u, "id": str(u["id"])} for u in users],
        "invitations": [{**i, "id": str(i["id"]), "expires_at": i["expires_at"].isoformat(), "created_at": i["created_at"].isoformat()} for i in invites],
        "templates": [{**t, "id": str(t["id"]), "created_at": t["created_at"].isoformat()} for t in templates],
        "registrations": [{**r, "id": str(r["id"]), "user_id": str(r["user_id"]), "submitted_at": r["submitted_at"].isoformat(),
                           "decided_at": None if r["decided_at"] is None else r["decided_at"].isoformat()} for r in regs],
    }


class RegistrationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    number: str = Field(min_length=3, max_length=40)
    register: Literal["NER", "RPEQ", "STATE"]
    state_scheme: str | None = Field(default=None, max_length=80)
    evidence: str = Field(min_length=15, max_length=1000)


@router.post("/admin/registrations")
def submit_registration(body: RegistrationBody, user: User, dsn: Dsn) -> dict[str, Any]:
    """The firm submits an approver's registration number; a platform administrator verifies it. Nothing changes the number until then."""
    rid = _call(dsn, user, "select registration_submit(%s, %s, %s, %s, %s) as id", (body.user_id, body.number, body.register, body.state_scheme, body.evidence))
    return {"registration_id": str(rid), "status": "submitted"}


# ------------------------------------------------------------------------------------------------------------------------ people
class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    role: Literal["designer", "checker", "approver"]


@router.post("/admin/invitations")
def invite(body: InviteBody, user: User, dsn: Dsn, mailer: Annotated[Any, Depends(get_mailer)]) -> dict[str, Any]:
    email = body.email.strip().lower()
    if not EMAIL.match(email):
        raise _err(422, "invalid", "that is not an e-mail address")
    iid = _call(dsn, user, "select admin_invite(%s, %s::user_role) as id", (email, body.role))
    sent = False
    if mailer is not None:
        with _as_user(dsn, user) as conn:
            firm = conn.execute("select name from firm where id = %s", (user.firm_id,)).fetchone()
        sent = bool(mailer.invitation(email, firm["name"] if firm else "your firm", body.role))
    return {"invitation_id": str(iid), "email_sent": sent}


@router.delete("/admin/invitations/{invitation_id}")
def revoke_invitation(invitation_id: UUID, user: User, dsn: Dsn) -> dict[str, Any]:
    _call(dsn, user, "select admin_revoke_invitation(%s)", (invitation_id,))
    return {"revoked": True}


class UserPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["designer", "checker", "approver"] | None = None
    is_admin: bool | None = None
    active: bool | None = None


@router.put("/admin/users/{user_id}")
def patch_user(user_id: UUID, body: UserPatch, user: User, dsn: Dsn) -> dict[str, Any]:
    if body.role is None and body.is_admin is None and body.active is None:
        raise _err(422, "invalid", "nothing to change")
    try:
        with _as_user(dsn, user) as conn:
            if body.role is not None:
                conn.execute("select admin_set_role(%s, %s::user_role)", (user_id, body.role))
            if body.is_admin is not None:
                conn.execute("select admin_set_admin(%s, %s)", (user_id, body.is_admin))
            if body.active is not None:
                conn.execute("select admin_set_active(%s, %s)", (user_id, body.active))
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    except psycopg.errors.RaiseException as exc:
        raise _err(422, "refused", str(exc).splitlines()[0]) from None
    return {"updated": True}


# ------------------------------------------------------------------------------------------------------------------------ settings
class FirmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    signer_mode: Literal["strict", "small_firm"]
    sample_size: int = Field(ge=1, le=1000)
    near_miss_default: float | None = Field(default=None, gt=0, le=0.5)
    void_clearance_mm: float | None = Field(default=None, ge=0, le=1000)


@router.put("/admin/firm")
def put_firm(body: FirmBody, user: User, dsn: Dsn) -> dict[str, Any]:
    _call(dsn, user, "select admin_set_settings(%s::text, %s::text, %s::int, %s::numeric, %s::numeric)",
          (body.name, body.signer_mode, body.sample_size, body.near_miss_default, body.void_clearance_mm))
    return {"saved": True}


# ------------------------------------------------------------------------------------------------------------------------ templates
def validate_title_block(data: bytes) -> str:
    import io

    import ezdxf
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8", "replace")))
    except Exception:  # noqa: BLE001 - any parse failure is a refusal, not a crash
        raise _err(422, "invalid_template", "the title block must be a readable DXF drawing") from None
    if len(doc.modelspace()) == 0 and not [b for b in doc.blocks if not b.name.startswith(("*", "_"))]:
        raise _err(422, "invalid_template", "the title block drawing is empty")
    return "image/vnd.dxf"


def validate_layer_standard(data: bytes) -> str:
    try:
        doc = json.loads(data.decode("utf-8"))
        layers = doc["layers"]
        assert isinstance(layers, dict) and 1 <= len(layers) <= 500
        for name, spec in layers.items():
            assert LAYER_NAME.match(name) and isinstance(spec, dict)
            assert isinstance(spec.get("color"), int) and not isinstance(spec.get("color"), bool) and 1 <= spec["color"] <= 255
            assert spec.get("linetype", "CONTINUOUS") in ("CONTINUOUS", "DASHED", "DASHDOT", "CENTER", "HIDDEN", "PHANTOM", "DOT")
        mapping = doc.get("map", {})
        assert isinstance(mapping, dict) and len(mapping) <= 200
        assert all(isinstance(k, str) and LAYER_NAME.match(k) and isinstance(v, str) and LAYER_NAME.match(v) for k, v in mapping.items())
        assert not (set(mapping) & set(mapping.values())) and not any(v.upper() in ("0", "DEFPOINTS") for v in mapping.values())
    except (ValueError, KeyError, TypeError, AssertionError):
        raise _err(422, "invalid_template", 'a layer standard is JSON like {"layers": {"A-DUCT": {"color": 3, "linetype": "CONTINUOUS"}}, "map": {"M-DUCT-RECT": "A-DUCT"}} ("map" optional: skill layer to your layer)') from None
    return "application/json"


@router.post("/admin/templates")
async def add_template(user: User, dsn: Dsn, kind: Annotated[Literal["title_block", "layer_standard"], Form()], name: Annotated[str, Form(max_length=120)],
                       file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    _require_admin(dsn, user)
    data = await file.read(MAX_TEMPLATE_BYTES + 1)
    if not data or len(data) > MAX_TEMPLATE_BYTES:
        raise _err(413 if data else 422, "bad_size", "a template is between 1 byte and 2 MiB")
    media = validate_title_block(data) if kind == "title_block" else validate_layer_standard(data)
    tid = _call(dsn, user, "select admin_add_template(%s, %s, %s, %s, %s) as id", (kind, name.strip(), media, data, hashlib.sha256(data).hexdigest()))
    return {"template_id": str(tid), "kind": kind, "sha256": hashlib.sha256(data).hexdigest()}


@router.get("/admin/templates/{template_id}/download")
def download_template(template_id: UUID, user: User, dsn: Dsn) -> Response:
    _require_admin(dsn, user)
    with _as_user(dsn, user) as conn:
        row = conn.execute("select * from admin_template_content(%s)", (template_id,)).fetchone()
    if row is None:
        raise _err(404, "not_found", "no such template")
    return Response(content=bytes(row["content"]), media_type=row["media_type"],
                    headers={"Content-Disposition": 'attachment; filename="template"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


# ------------------------------------------------------------------------------------------------------------------------ joining
@router.get("/invitations/mine")
def my_invitations(subject: Annotated[Any, Depends(token_subject)], dsn: Dsn) -> dict[str, Any]:
    """For a person who has signed in but belongs to no firm yet: is there an invitation for the address they signed in with?"""
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        rows = conn.execute(
            "select i.id::text as id, i.role::text as role, f.name as firm from invitation i join firm f on f.id = i.firm_id join auth.users a on lower(a.email) = i.email"
            " where a.id = %s and a.email_confirmed_at is not null and i.status = 'pending' and i.expires_at > now() order by i.created_at desc",
            (subject,)).fetchall()
        row = rows[0] if len(rows) == 1 else None
        member = conn.execute("select 1 from app_user where id = %s", (subject,)).fetchone()
    return {"member": member is not None, "invitation": row, "invitations": rows}


class AcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitation_id: UUID | None = None


@router.post("/invitations/accept")
def accept(subject: Annotated[Any, Depends(token_subject)], dsn: Dsn, body: AcceptBody | None = None) -> dict[str, Any]:
    try:
        with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": str(subject), "role": "authenticated"}),))
            firm = conn.execute("select accept_invitation(%s) as f", (body.invitation_id if body else None,)).fetchone()
    except psycopg.errors.RaiseException as exc:
        raise _err(422, "refused", str(exc).splitlines()[0]) from None
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    return {"joined": True, "firm_id": str(firm["f"]) if firm else None}
