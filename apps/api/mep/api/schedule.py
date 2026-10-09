"""Schedule endpoints: Excel import and form entry.

No database code and no auth here. The repository, the rule pack and the signed-in user are injected
(FastAPI dependencies); the defaults refuse every request, so a router mounted without wiring is closed, not open.
Provenance is decided by the server: an Excel import is 'extracted', a form entry is 'default'. Nothing here
decides compliance.
"""
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from mep.engine.loader import RulePack
from mep.ingest.schedule import (
    NAME_RE,
    CellError,
    normalise_value,
    read_schedule,
    schedule_inputs,
    system_types,
)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_SYSTEMS = 2000
IMPORT_ROLES = frozenset({"designer"})
FORM_ROLES = frozenset({"designer"})

router = APIRouter()


class RevisionFrozenError(Exception):
    """The revision is frozen; its schedule can no longer change."""


class InputsChangedError(Exception):
    """An input changed between reading it for a run and storing the run's results."""


@dataclass(frozen=True)
class CurrentUser:
    user_id: UUID
    firm_id: UUID
    role: str


class ScheduleRepository(Protocol):
    def revision_edition(self, revision_id: UUID, firm_id: UUID) -> str | None:
        """The NCC edition of the revision's project, or None if the revision is not in this firm."""

    def upsert_systems(self, revision_id: UUID, firm_id: UUID, systems: Sequence["SystemModel"], source: str) -> int:
        """Store systems and their inputs by tag. Raises RevisionFrozenError for a frozen revision."""


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_repository() -> ScheduleRepository:
    raise HTTPException(status_code=503, detail="repository is not configured")


def get_pack() -> RulePack:
    raise HTTPException(status_code=503, detail="rule pack is not configured")


class ScheduleInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    value: StrictBool | StrictInt | StrictFloat | StrictStr
    unit: str | None = None
    provenance: Literal["default", "extracted"] = "default"

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.fullmatch(v):
            raise ValueError("name must match ^[a-z][a-z0-9_]{0,63}$")
        return v

    @field_validator("unit")
    @classmethod
    def _unit(cls, v: str | None) -> str | None:
        if v is not None and not 0 < len(v) <= 64:
            raise ValueError("unit must be 1 to 64 characters")
        return v

    @model_validator(mode="after")
    def _checks(self) -> "ScheduleInputModel":
        is_number = isinstance(self.value, int | float) and not isinstance(self.value, bool)
        if is_number and not math.isfinite(self.value):  # type: ignore[arg-type]
            raise ValueError("value must be a finite number")
        if is_number and not self.unit:
            raise ValueError("a numeric value needs a unit")
        if not is_number and self.unit:
            raise ValueError("only numbers have a unit")
        return self


class SystemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=64)
    system_type: str = Field(min_length=1, max_length=64)
    inputs: list[ScheduleInputModel] = Field(default_factory=list, max_length=500)

    @field_validator("tag", "system_type")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v

    @model_validator(mode="after")
    def _unique(self) -> "SystemModel":
        names = [i.name for i in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError("an input is listed twice")
        return self


class FormSystemsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    systems: list[SystemModel] = Field(max_length=MAX_SYSTEMS)

    @model_validator(mode="after")
    def _form_is_default(self) -> "FormSystemsRequest":
        tags = [s.tag for s in self.systems]
        if len(tags) != len(set(tags)):
            raise ValueError("a system tag is used twice")
        if any(i.provenance != "default" for s in self.systems for i in s.inputs):
            raise ValueError("form entries are provenance 'default'; confirmation happens at Gate 1")
        return self


class SaveResponse(BaseModel):
    systems: int
    inputs: int


User = Annotated[CurrentUser, Depends(current_user)]
Repo = Annotated[ScheduleRepository, Depends(get_repository)]
Pack = Annotated[RulePack, Depends(get_pack)]


def _edition(repo: ScheduleRepository, revision_id: UUID, user: CurrentUser) -> str:
    edition = repo.revision_edition(revision_id, user.firm_id)
    if edition is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return edition


def _save(repo: ScheduleRepository, revision_id: UUID, user: CurrentUser, systems: list[SystemModel],
          source: str) -> SaveResponse:
    try:
        repo.upsert_systems(revision_id, user.firm_id, systems, source)
    except RevisionFrozenError:
        raise HTTPException(status_code=409, detail="revision is frozen") from None
    return SaveResponse(systems=len(systems), inputs=sum(len(s.inputs) for s in systems))


@router.post("/revisions/{revision_id}/schedule/import")
def import_schedule(revision_id: UUID, user: User, repo: Repo, pack: Pack,
                    file: Annotated[UploadFile, File()]) -> SaveResponse:
    if user.role not in IMPORT_ROLES:
        raise HTTPException(status_code=403, detail="only a designer imports a schedule")
    edition = _edition(repo, revision_id, user)
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail={"errors": [{"row": None, "column": None,
                                                                 "message": "only .xlsx files are accepted"}]})
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")
    result = read_schedule(data, pack, edition)
    if result.errors:  # all or nothing: a schedule with problems saves no part of itself
        raise HTTPException(status_code=422, detail={"errors": [
            {"row": e.row, "column": e.column, "message": e.message} for e in result.errors]})
    systems = [SystemModel(tag=r.tag, system_type=r.system_type, inputs=[
        ScheduleInputModel(name=i.name, value=i.value, unit=i.unit, provenance="extracted")
        for i in r.inputs]) for r in result.rows]
    return _save(repo, revision_id, user, systems, "xlsx")


def _refuse_constant(token: str) -> None:
    raise ValueError(f"{token} is not a number")


@router.post("/revisions/{revision_id}/schedule/systems")
async def post_systems(revision_id: UUID, request: Request, user: User, repo: Repo, pack: Pack) -> SaveResponse:
    if user.role not in FORM_ROLES:
        raise HTTPException(status_code=403, detail="not allowed to edit the schedule")
    edition = _edition(repo, revision_id, user)
    # parsed by hand so NaN/Infinity are refused and never echoed back (they cannot be serialised)
    try:
        raw = await request.body()
        body = FormSystemsRequest.model_validate(json.loads(raw, parse_constant=_refuse_constant))
    except (ValueError, ValidationError) as exc:
        detail = (exc.errors(include_input=False, include_context=False, include_url=False)
                  if isinstance(exc, ValidationError) else [{"msg": "body is not valid JSON"}])
        raise HTTPException(status_code=422, detail=detail) from None
    specs = schedule_inputs(pack, edition)
    types = system_types(pack, edition)
    errors: list[dict[str, str | None]] = []
    systems: list[SystemModel] = []
    for s in body.systems:
        if types is not None and s.system_type not in types:
            errors.append({"system": s.tag, "input": None, "message": f"system type must be one of {list(types)}"})
        inputs: list[ScheduleInputModel] = []
        for i in s.inputs:
            spec = specs.get(i.name)
            if spec is None:
                errors.append({"system": s.tag, "input": i.name, "message": f"not an input of {edition}"})
                continue
            try:
                value, unit = normalise_value(spec, i.value, i.unit)
            except CellError as exc:
                errors.append({"system": s.tag, "input": i.name, "message": str(exc)})
                continue
            inputs.append(ScheduleInputModel(name=i.name, value=value, unit=unit, provenance="default"))
        systems.append(SystemModel(tag=s.tag, system_type=s.system_type, inputs=inputs))
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})
    return _save(repo, revision_id, user, systems, "form")
