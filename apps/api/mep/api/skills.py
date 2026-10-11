"""Drafting skills in the app: the list and each spec card as a form, firm defaults, the plain-language shortcut, running a skill, the runs of a
revision, downloads of released files, and feeding a built IFC/DXF into the revision as an (unconfirmed) architect model.

A file is returned to anybody only if its artifact is `released`, which happens only when the skill's validator AND the runner's second,
separate re-check both passed. Nothing here decides compliance.
"""
import hashlib
import json
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
from mep.skills_runner.runner import SkillRunResult, SkillUnavailable
from mep.skills_runner.shortcut import missing_fields, parse_shortcut

router = APIRouter()
DESIGNER_ROLES = frozenset({"designer"})


class SkillsService(Protocol):
    def firm_defaults(self, skill: str) -> dict[str, Any]: ...
    def set_firm_defaults(self, skill: str, defaults: dict[str, Any]) -> None: ...
    def revision_frozen(self, revision_id: UUID) -> bool | None: ...
    def execute(self, revision_id: UUID, skill: str, spec: dict[str, Any]) -> SkillRunResult: ...
    def confirm_card(self, revision_id: UUID, skill: str, digest: str, note_id: UUID | None) -> None: ...
    def card_confirmed(self, revision_id: UUID, skill: str, digest: str) -> bool: ...
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
    fields = skill_form.default_fields(skill_form.build_form(_skill(name).schema))
    problems = [f"{p}: {why}" for p, v in body.defaults.items() if (why := skill_form.default_problem(fields[p], v))]
    if problems or len(json.dumps(body.defaults)) > 4000:
        raise _err(422, "bad_default", "; ".join(problems[:5]) or "the defaults are too large")
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
    spec = skill_form.apply_constants(_skill(name).schema, spec)
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


def card_digest(spec: dict[str, Any]) -> str:
    """The version of a spec card: a hash of the EFFECTIVE card (firm defaults and constants applied) as it will be built."""
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def effective_spec(svc: SkillsService, name: str, spec_in: dict[str, Any], use_firm_defaults: bool = True) -> tuple[dict[str, Any], list[str]]:
    s = get_skill(name)
    spec, filled = (skill_form.apply_defaults(s.schema, spec_in, svc.firm_defaults(name)) if use_firm_defaults else (spec_in, []))
    return skill_form.apply_constants(s.schema, spec), filled


def server_filled(svc: Any, name: str, revision_id: UUID, spec: dict[str, Any]) -> dict[str, Any]:
    """Let the service put revision facts into the card (hvac-dxf's sizing schedule); services without the hook leave the card as it is."""
    fill = getattr(svc, "server_fill", None)
    return spec if fill is None else fill(name, revision_id, spec)  # type: ignore[no-any-return]


def perform_run(svc: SkillsService, user: CurrentUser, revision_id: UUID, name: str, spec_in: dict[str, Any], *,
                use_firm_defaults: bool = True, via: str = "user",
                expected_digest: str | None = None) -> tuple[SkillRunResult, dict[str, Any], list[str]]:
    """Run a skill for a designer on an open revision of their firm and record it: the one door used by the API and by the designer agent.
    Raises SkillsRefused (404 unknown skill / revision, 403 not a designer, 409 frozen, 503 unavailable)."""
    if user.role not in DESIGNER_ROLES:
        raise SkillsRefused(403, "forbidden", "only a designer runs a drafting skill")
    try:
        get_skill(name)
    except UnknownSkill:
        raise SkillsRefused(404, "unknown_skill", f"no skill named {name!r}") from None
    frozen = svc.revision_frozen(revision_id)
    if frozen is None:
        raise SkillsRefused(404, "not_found", "revision not found")
    if frozen:
        raise SkillsRefused(409, "revision_frozen", "revision is frozen")
    spec, filled = effective_spec(svc, name, spec_in, use_firm_defaults)
    spec = server_filled(svc, name, revision_id, spec)
    if expected_digest is not None and card_digest(spec) != expected_digest:
        raise SkillsRefused(409, "card_changed", "the card (or the firm defaults) changed since it was confirmed: confirm the new version")
    try:
        result = svc.execute(revision_id, name, spec)
    except SkillUnavailable as exc:
        raise SkillsRefused(503, "skill_unavailable", str(exc)) from None
    return result, svc.record(revision_id, name, spec, result, via), filled


class CardBody(BaseModel):
    spec: dict[str, Any]
    use_firm_defaults: bool = True
    spec_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    note_id: UUID | None = None


@router.post("/revisions/{revision_id}/skills/{name}/card-preview")
def card_preview(revision_id: UUID, name: str, body: CardBody, user: User, svc: Service) -> dict[str, Any]:
    """The exact card that would be built (defaults and constants applied) and its version hash: what a designer reads before confirming."""
    _designer(user)
    _skill(name)
    spec, filled = effective_spec(svc, name, body.spec, body.use_firm_defaults)
    try:
        spec = server_filled(svc, name, revision_id, spec)
    except SkillsRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
    return {"effective_spec": spec, "spec_sha256": card_digest(spec), "firm_defaults_applied": filled,
            "confirmed": svc.card_confirmed(revision_id, name, card_digest(spec))}


@router.post("/revisions/{revision_id}/skills/{name}/confirm-card")
def confirm_card(revision_id: UUID, name: str, body: CardBody, user: User, svc: Service) -> dict[str, Any]:
    """A designer confirms ONE version of a card. An agent may build only a card version confirmed here."""
    _designer(user)
    _skill(name)
    spec, _ = effective_spec(svc, name, body.spec, body.use_firm_defaults)
    try:
        spec = server_filled(svc, name, revision_id, spec)
    except SkillsRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
    digest = card_digest(spec)
    if body.spec_sha256 is not None and body.spec_sha256 != digest:
        raise _err(409, "card_changed", "the card changed since you were shown it: review the new version")
    try:
        svc.confirm_card(revision_id, name, digest, body.note_id)
    except SkillsRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
    return {"confirmed": True, "spec_sha256": digest}


@router.post("/revisions/{revision_id}/skills/{name}/run")
def run(revision_id: UUID, name: str, body: RunBody, user: User, svc: Service) -> dict[str, Any]:
    try:
        result, recorded, filled = perform_run(svc, user, revision_id, name, body.spec, use_firm_defaults=body.use_firm_defaults)
    except SkillsRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
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
