"""Drafting skills in the app: the list and each spec card as a form, firm defaults, the plain-language shortcut, running a skill, the runs of a
revision, downloads of released files, and feeding a built IFC/DXF into the revision as an (unconfirmed) architect model.

A file is returned to anybody only if its artifact is `released`, which happens only when the skill's validator AND the runner's second,
separate re-check both passed. Nothing here decides compliance.
"""
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mep.api.schedule import CurrentUser
from mep.api.skills_pg import SkillsRefused
from mep.api.uploads import UploadRefused, UploadService
from mep.skills_runner import form as skill_form
from mep.skills_runner.registry import UnknownSkill, availability, get_skill, list_skills
from mep.skills_runner.runner import SkillRunResult, SkillUnavailable, run_skill
from mep.skills_runner.shortcut import missing_fields, parse_shortcut

router = APIRouter()
DESIGNER_ROLES = frozenset({"designer"})


class SkillsService(Protocol):
    def firm_defaults(self, skill: str) -> dict[str, Any]: ...
    def set_firm_defaults(self, skill: str, defaults: dict[str, Any]) -> None: ...
    def revision_frozen(self, revision_id: UUID) -> bool | None: ...
    def record(self, revision_id: UUID, skill: str, spec: dict[str, Any], result: SkillRunResult, via: str = "user") -> dict[str, Any]: ...
    def runs(self, revision_id: UUID) -> list[dict[str, Any]]: ...
    def spec_of(self, revision_id: UUID, run_id: UUID) -> tuple[str, dict[str, Any]] | None: ...
    def download(self, artifact_id: UUID) -> tuple[str, str, bytes] | None: ...
    def released_file(self, revision_id: UUID, run_id: UUID, role: str) -> tuple[str, bytes] | None: ...


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_service(user: Annotated[CurrentUser, Depends(current_user)]) -> SkillsService:
    raise HTTPException(status_code=503, detail="skills are not configured")


def get_uploads() -> UploadService:
    raise HTTPException(status_code=503, detail={"code": "uploads_unavailable", "message": "uploads are not configured"})


User = Annotated[CurrentUser, Depends(current_user)]
Service = Annotated[SkillsService, Depends(get_service)]
Uploads = Annotated[UploadService, Depends(get_uploads)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _skill(name: str) -> Any:
    try:
        return get_skill(name)
    except UnknownSkill:
        raise _err(404, "unknown_skill", f"no skill named {name!r}") from None


def _designer(user: CurrentUser) -> None:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer runs a drafting skill")


class RunBody(BaseModel):
    spec: dict[str, Any]
    use_firm_defaults: bool = True


class DefaultsBody(BaseModel):
    defaults: dict[str, Any] = Field(default_factory=dict)


class ShortcutBody(BaseModel):
    text: Annotated[str, Field(min_length=3, max_length=2000)]


@router.get("/skills")
def skills(user: User) -> list[dict[str, Any]]:
    out = []
    for s in list_skills():
        ok, why = availability(s.name)
        out.append({"name": s.name, "description": s.description, "available": ok, "unavailable_reason": why})
    return out


@router.get("/skills/{name}/card")
def card(name: str, user: User, svc: Service) -> dict[str, Any]:
    s = _skill(name)
    form = skill_form.build_form(s.schema)
    return {"name": s.name, "description": s.description, "form": form, "firm_default_paths": sorted(skill_form.default_paths(form)),
            "firm_defaults": svc.firm_defaults(name), "available": availability(name)[0]}


@router.put("/skills/{name}/defaults")
def set_defaults(name: str, body: DefaultsBody, user: User, svc: Service) -> dict[str, Any]:
    _designer(user)
    allowed = skill_form.default_paths(skill_form.build_form(_skill(name).schema))
    bad = sorted(set(body.defaults) - allowed)
    if bad:
        raise _err(422, "not_a_default", "these fields are engineer inputs and cannot have a firm default: " + ", ".join(bad))
    svc.set_firm_defaults(name, body.defaults)
    return {"saved": True, "defaults": body.defaults}


@router.post("/skills/{name}/shortcut")
def shortcut(name: str, body: ShortcutBody, user: User, svc: Service) -> dict[str, Any]:
    """Read a typed sentence into a PROPOSED card; the designer reviews every value and confirms before anything is built."""
    _skill(name)
    try:
        parsed = parse_shortcut(name, body.text)
    except KeyError:
        raise _err(422, "no_shortcut", f"{name} has no plain-language shortcut") from None
    spec, filled = skill_form.apply_defaults(_skill(name).schema, parsed.spec, svc.firm_defaults(name))
    return {"proposal": spec, "understood": parsed.understood, "assumptions": parsed.assumptions,
            "firm_defaults_applied": filled, "missing": missing_fields(name, spec)}


@router.post("/skills/{name}/missing")
def missing(name: str, body: RunBody, user: User) -> dict[str, Any]:
    """Which required fields a card still lacks, as plain-language questions."""
    _skill(name)
    return {"missing": missing_fields(name, body.spec)}


def _summary(result: SkillRunResult, recorded: dict[str, Any], filled: list[str]) -> dict[str, Any]:
    return {"run_id": recorded["run_id"], "status": result.status, "released": result.released, "message": result.message,
            "validation": result.validation, "files": recorded["artifacts"], "firm_defaults_applied": filled}


@router.post("/revisions/{revision_id}/skills/{name}/run")
def run(revision_id: UUID, name: str, body: RunBody, user: User, svc: Service) -> dict[str, Any]:
    _designer(user)
    s = _skill(name)
    frozen = svc.revision_frozen(revision_id)
    if frozen is None:
        raise _err(404, "not_found", "revision not found")
    if frozen:
        raise _err(409, "revision_frozen", "revision is frozen")
    spec, filled = (skill_form.apply_defaults(s.schema, body.spec, svc.firm_defaults(name)) if body.use_firm_defaults else (body.spec, []))
    try:
        result = run_skill(name, spec)
    except SkillUnavailable as exc:
        raise _err(503, "skill_unavailable", str(exc)) from None
    recorded = svc.record(revision_id, name, spec, result)
    return _summary(result, recorded, filled)


@router.get("/revisions/{revision_id}/skill-runs")
def runs(revision_id: UUID, user: User, svc: Service) -> list[dict[str, Any]]:
    return svc.runs(revision_id)


@router.get("/artifacts/{artifact_id}/download")
def download(artifact_id: UUID, user: User, svc: Service) -> Response:
    got = svc.download(artifact_id)
    if got is None:          # unknown, another firm's, and unreleased all look the same
        raise _err(404, "not_found", "no such released file")
    name, media_type, content = got
    return Response(content=content, media_type=media_type, headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.post("/revisions/{revision_id}/skill-runs/{run_id}/use-as-model")
def use_as_model(revision_id: UUID, run_id: UUID, user: User, svc: Service, uploads: Uploads,
                 authorization: Annotated[str | None, Header()] = None,
                 which: Literal["ifc", "dxf"] = "ifc") -> dict[str, Any]:
    """Feed a built (released) IFC or DXF through the normal upload path: its spaces arrive as 'extracted' and need Gate 1 like any model."""
    _designer(user)
    found = svc.released_file(revision_id, run_id, which)
    if found is None:
        raise _err(404, "not_found", f"this run has no released {which} file")
    name, data = found
    token = (authorization or "").partition(" ")[2].strip()
    try:
        return uploads.ingest(user=user, token=token, revision_id=revision_id, name=name, kind=which, data=data)
    except UploadRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None


__all__ = ["SkillsRefused", "router"]
