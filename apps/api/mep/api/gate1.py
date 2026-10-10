"""Gate 1 endpoints: building parts, spaces, system inputs, confirmation, and the run that is blocked until confirmed.

No database code and no auth here: the repository, the rule pack and the signed-in user are injected, and the
defaults refuse every request. Provenance is decided by the server. A client write always lands as 'default' (any
provenance in the body is ignored); only `confirm`, callable by a designer, makes a row engineer_confirmed. The
role comes from the injected user, never from the request. Nothing here decides compliance: the run goes through
mep.engine.runner.run and its refusal is returned verbatim.
"""
import math
import uuid
from datetime import date
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from mep.api.schedule import CurrentUser, InputsChangedError, RevisionFrozenError
from mep.engine.loader import RulePack
from mep.engine.model import BuildingPart, InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.runner import RunRefused, run

CONFIRM_ROLES = frozenset({"designer"})
MAX_PARTS = 50
MAX_AREA_M2 = 10_000_000
MAX_CONFIRM_ROWS = 5000
BUILDING_PART = "building_part"   # the schedule input that names the building part a system belongs to

router = APIRouter()


class UnknownSystemError(Exception):
    """An input names a system tag that does not exist in this revision (or names none)."""


class InvalidInputError(Exception):
    """A Gate 1 input is not one the project's edition declares, or its unit/value does not fit the declaration."""


class EvidenceLockedError(Exception):
    """A value read from a drawing cannot be edited or relabelled as hand-entered."""


class ConfirmRefused(Exception):
    """The store refused a confirmation (for example the project facts are incomplete). The message is shown."""


class Gate1Repository(Protocol):
    """Storage for Gate 1. Every method is scoped to the firm; a revision of another firm does not exist."""

    def get_gate1_view(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None:
        """{parts, spaces, inputs, health, ncc_edition, project} or None. health is the stored ingest health;
        project is {id, state, ncc_edition, climate_zone, approval_date, confirmed} so the designer sees (and
        confirms, kind 'project') the facts that choose the rule pack."""

    def revision_frozen(self, revision_id: UUID, firm_id: UUID) -> bool: ...

    def replace_parts(self, revision_id: UUID, firm_id: UUID, parts: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

    def upsert_space(self, revision_id: UUID, firm_id: UUID, space_id: str | None, data: dict[str, Any],
                     manual_trace: bool) -> dict[str, Any] | None:
        """Insert (space_id None) or edit. The store forces provenance 'default' and withdraws any confirmation."""

    def upsert_input(self, revision_id: UUID, firm_id: UUID, input_id: str | None,
                     data: dict[str, Any]) -> dict[str, Any] | None: ...

    def confirm(self, groups: dict[str, list[str]], user_id: UUID, revision_id: UUID | None = None,
                etags: dict[str, str | None] | None = None) -> None:
        """Mark rows engineer_confirmed (kind -> ids), record confirmed_by and ledger it, ALL in one transaction. `etags`
        maps "kind:id" to the version the designer was shown. Only called for a designer. Raises ConfirmRefused."""

    def load_run_inputs(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any]:
        """{project: {state, ncc_edition, climate_zone, building_class, approval_date, confirmed_by}, spaces: [{id, provenance,
        confirmed_by}], systems: [{id, rules, part, inputs: [{name, value, unit, provenance, confirmed_by}]}]}"""


def get_ledger() -> Any:
    """The append-only ledger writer. No default: a run that cannot record a refusal must not run."""
    raise HTTPException(status_code=503, detail={"code": "ledger_unavailable", "message": "ledger is not configured"})


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_repository() -> Gate1Repository:
    raise HTTPException(status_code=503, detail="repository is not configured")


def get_pack() -> RulePack:
    raise HTTPException(status_code=503, detail="rule pack is not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Repo = Annotated[Gate1Repository, Depends(get_repository)]
Pack = Annotated[RulePack, Depends(get_pack)]
Ledger = Annotated[Any, Depends(get_ledger)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


class PartModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    building_class: Literal["2", "3", "4", "5", "6", "7a", "7b", "8", "9a", "9b", "9c"]
    storeys: Annotated[int, Field(strict=True, ge=1, le=200)]
    area_m2: Annotated[float, Field(gt=0, le=MAX_AREA_M2, allow_inf_nan=False)]


class PartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: Annotated[list[PartModel], Field(min_length=1, max_length=MAX_PARTS)]


Number = Annotated[StrictInt | StrictFloat, Field(allow_inf_nan=False)]


class SpaceModel(BaseModel):
    """Client-writable space fields. Provenance and confirmation are not here; unknown keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    ifc_guid: Annotated[str, Field(max_length=64)] | None = None
    name: Annotated[str, Field(max_length=200)] | None = None
    area_m2: Annotated[Number, Field(ge=0, le=MAX_AREA_M2)] | None = None
    use: Annotated[str, Field(max_length=100)] | None = None
    storey: StrictInt | Annotated[StrictStr, Field(max_length=100)] | None = None
    ceiling_void_mm: Annotated[Number, Field(ge=0, le=100_000)] | None = None
    manual_trace: StrictBool = False

    @model_validator(mode="after")
    def _manual(self) -> "SpaceModel":
        if self.manual_trace and self.ifc_guid is not None:
            raise ValueError("a manual-trace space has no IFC source")
        return self


class InputModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    system: Annotated[str, Field(min_length=1, max_length=64)] | None = None   # schedule tag of the owning system
    value: StrictBool | StrictInt | StrictFloat | StrictStr | None = None
    unit: Annotated[str, Field(min_length=1, max_length=64)] | None = None

    @field_validator("value")
    @classmethod
    def _finite(cls, v: Any) -> Any:
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("value must be a finite number")
        return v

    @model_validator(mode="after")
    def _unit(self) -> "InputModel":
        is_number = isinstance(self.value, int | float) and not isinstance(self.value, bool)
        if is_number and not self.unit:
            raise ValueError("a numeric value needs a unit")
        if not is_number and self.unit:
            raise ValueError("only numbers have a unit")
        return self


class RowRefModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["space", "system_input", "building_part", "project"]
    id: Annotated[str, Field(min_length=1, max_length=64)]
    etag: Annotated[str, Field(max_length=64)] | None = None      # the row version the designer was shown


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: Annotated[list[RowRefModel], Field(min_length=1, max_length=MAX_CONFIRM_ROWS)]


def _view(repo: Gate1Repository, revision_id: UUID, user: CurrentUser) -> dict[str, Any]:
    view = repo.get_gate1_view(revision_id, user.firm_id)
    if view is None:
        raise _err(404, "not_found", "revision not found")
    return view


def _editable(repo: Gate1Repository, revision_id: UUID, user: CurrentUser) -> dict[str, Any]:
    view = _view(repo, revision_id, user)
    if repo.revision_frozen(revision_id, user.firm_id):
        raise _err(409, "revision_frozen", "revision is frozen")
    return view


@router.get("/revisions/{revision_id}/gate1")
def get_gate1(revision_id: UUID, user: User, repo: Repo) -> dict[str, Any]:
    v = _view(repo, revision_id, user)
    out = {k: v[k] for k in ("parts", "spaces", "inputs", "health", "ncc_edition", "project") if k in v}
    out["role"] = user.role          # so the UI can explain why Confirm is unavailable; the server still enforces it
    return out


@router.put("/revisions/{revision_id}/gate1/parts")
def put_parts(revision_id: UUID, body: PartsRequest, user: User, repo: Repo) -> list[dict[str, Any]]:
    _require_designer(user)
    _editable(repo, revision_id, user)
    try:
        return repo.replace_parts(revision_id, user.firm_id, [p.model_dump() for p in body.parts])
    except RevisionFrozenError:
        raise _err(409, "revision_frozen", "revision is frozen") from None


def _write(repo: Gate1Repository, revision_id: UUID, user: CurrentUser, kind: str, row_id: str | None,
           data: dict[str, Any], manual: bool = False) -> dict[str, Any]:
    _require_designer(user)
    view = _editable(repo, revision_id, user)
    if row_id is not None and row_id not in {r["id"] for r in view["spaces" if kind == "space" else "inputs"]}:
        raise _err(404, "not_found", f"{kind} not found")
    try:
        if kind == "space":
            row = repo.upsert_space(revision_id, user.firm_id, row_id, data, manual)
        else:
            row = repo.upsert_input(revision_id, user.firm_id, row_id, data)
    except RevisionFrozenError:
        raise _err(409, "revision_frozen", "revision is frozen") from None
    except UnknownSystemError as exc:
        raise _err(422, "unknown_system", str(exc) or "name the schedule tag of an existing system") from None
    except InvalidInputError as exc:
        raise _err(422, "invalid_input", str(exc)) from None
    except EvidenceLockedError as exc:
        raise _err(409, "evidence_locked", str(exc)) from None
    if row is None:
        raise _err(404, "not_found", f"{kind} not found")
    return row


def _space_data(body: SpaceModel, *, partial: bool = False) -> tuple[dict[str, Any], bool]:
    """An edit (PUT) carries only the fields the client sent; the others keep their stored value."""
    return body.model_dump(exclude={"manual_trace"}, exclude_unset=partial), body.manual_trace


@router.post("/revisions/{revision_id}/gate1/spaces")
def add_space(revision_id: UUID, body: SpaceModel, user: User, repo: Repo) -> dict[str, Any]:
    data, manual = _space_data(body)
    return _write(repo, revision_id, user, "space", None, data, manual)


@router.put("/revisions/{revision_id}/gate1/spaces/{space_id}")
def update_space(revision_id: UUID, space_id: str, body: SpaceModel, user: User, repo: Repo) -> dict[str, Any]:
    data, manual = _space_data(body, partial=True)
    return _write(repo, revision_id, user, "space", space_id, data, manual)


@router.post("/revisions/{revision_id}/gate1/inputs")
def add_input(revision_id: UUID, body: InputModel, user: User, repo: Repo) -> dict[str, Any]:
    return _write(repo, revision_id, user, "input", None, body.model_dump())


@router.put("/revisions/{revision_id}/gate1/inputs/{input_id}")
def update_input(revision_id: UUID, input_id: str, body: InputModel, user: User, repo: Repo) -> dict[str, Any]:
    return _write(repo, revision_id, user, "input", input_id, body.model_dump())


class PartAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part: Annotated[int, Field(strict=True, ge=0, le=MAX_PARTS - 1)]      # position in the project's building parts


@router.put("/revisions/{revision_id}/gate1/systems/{tag}/part")
def assign_part(revision_id: UUID, tag: str, body: PartAssignment, user: User, repo: Repo) -> dict[str, Any]:
    """A system names the building part it serves. It is an input like any other: it lands as 'default', a designer
    confirms it, an edit (or a change to the parts) withdraws the confirmation."""
    return _write(repo, revision_id, user, "input", None,
                  {"system": tag, "name": BUILDING_PART, "value": body.part, "unit": "dimensionless"})


@router.post("/revisions/{revision_id}/gate1/confirm")
def confirm(revision_id: UUID, body: ConfirmRequest, user: User, repo: Repo) -> dict[str, Any]:
    if user.role not in CONFIRM_ROLES:  # the role is the injected user's, never the request's
        raise _err(403, "forbidden", "only a designer confirms Gate 1 rows")
    view = _editable(repo, revision_id, user)
    known = {"space": {r["id"] for r in view["spaces"]}, "system_input": {r["id"] for r in view["inputs"]},
             "building_part": {r["id"] for r in view["parts"]},
             "project": {view["project"]["id"]} if view.get("project") else set()}
    for r in body.rows:
        if r.id not in known[r.kind]:
            raise _err(404, "not_found", f"{r.kind} {r.id} not found in this revision")
    groups = {kind: sorted({r.id for r in body.rows if r.kind == kind})
              for kind in ("space", "system_input", "building_part", "project")}
    groups = {k: v for k, v in groups.items() if v}
    try:
        repo.confirm(groups, user.user_id, revision_id, {f"{r.kind}:{r.id}": r.etag for r in body.rows})
    except ConfirmRefused as exc:
        raise _err(409, "cannot_confirm", str(exc)) from None
    except RevisionFrozenError:
        raise _err(409, "revision_frozen", "revision is frozen") from None
    return {"confirmed": [r.model_dump(exclude={"etag"}) for r in body.rows]}


def _refuse(code: str, reasons: list[str]) -> JSONResponse:
    return JSONResponse(status_code=409, content={"code": code, "message": "; ".join(reasons), "reasons": reasons})


def _require_designer(user: CurrentUser) -> None:
    if user.role not in CONFIRM_ROLES:  # who may change Gate 1 data: the injected user's role, never the request's
        raise _err(403, "forbidden", "only a designer changes Gate 1 data")


def _unconfirmed_parts(p: dict[str, Any]) -> list[str]:
    """Building parts choose the rule pack and applicability: each needs a recorded confirmation."""
    out = []
    if p.get("approval_date") is None:
        out.append("the approval date is not set, so the NCC edition in force cannot be decided")
    if p.get("confirmed_by") is None:
        out.append("the project facts (state, NCC edition, climate zone, approval date) are not confirmed at Gate 1")
    bc = p["building_class"]
    if not isinstance(bc, list):
        return [*out, "the building class has no recorded confirmation (enter building parts and confirm them)"]
    return out + [f"building part {i} is not confirmed at Gate 1" for i, x in enumerate(bc)
                  if x.get("confirmed_by") is None]


def _project(p: dict[str, Any], revision_id: UUID, firm_id: UUID) -> ProjectFacts:
    bc = p["building_class"]
    if isinstance(bc, list):
        bc = [BuildingPart(x["building_class"], x.get("storeys"), x.get("area_m2")) for x in bc]
    ad = p["approval_date"]
    # the engine refuses facts that are not engineer_confirmed: derive it from the recorded confirmation, never by default
    prov = Provenance.ENGINEER_CONFIRMED if p.get("confirmed_by") is not None else Provenance.DEFAULT
    return ProjectFacts(p["state"], p["ncc_edition"], p["climate_zone"], bc,
                        ad if isinstance(ad, date) else date.fromisoformat(ad),
                        firm_id=str(firm_id), revision_id=str(revision_id), facts_provenance=prov)


@router.post("/revisions/{revision_id}/run-rules")
def run_rules(revision_id: UUID, user: User, repo: Repo, pack: Pack, ledger: Ledger) -> Any:
    _view(repo, revision_id, user)
    if user.role not in CONFIRM_ROLES:
        raise _err(403, "forbidden", "only a designer runs the rules")
    data = repo.load_run_inputs(revision_id, user.firm_id)
    if data.get("frozen"):
        raise _err(409, "revision_frozen", "revision is frozen: its results are final; a new architect revision gets its own run")
    unconfirmed_parts = _unconfirmed_parts(data["project"])
    if unconfirmed_parts:
        return _refuse("gate1_required", unconfirmed_parts)
    rows = [("space", s["id"], s) for s in data.get("spaces", [])]
    for sysrow in data.get("systems", []):
        rows += [("input", f"{sysrow['id']}.{i['name']}", i) for i in sysrow.get("inputs", [])]
    extracted = [f"{k} {n}" for k, n, r in rows if r["provenance"] == Provenance.EXTRACTED.value]
    if extracted:
        return _refuse("extracted_inputs", [f"{x} is extracted and not confirmed at Gate 1" for x in extracted])
    unconfirmed = [f"{k} {n}" for k, n, r in rows if r.get("confirmed_by") is None]
    if unconfirmed:
        return _refuse("gate1_required", [f"{x} is not confirmed at Gate 1" for x in unconfirmed])
    pending = getattr(repo, "diff_reasons", None)
    if pending is not None:       # a child revision may not run until the designer has confirmed what changed
        reasons = pending(revision_id, user.firm_id)
        if reasons:
            return _refuse("diff_not_confirmed", reasons)
    classes = data["project"]["building_class"]
    n_parts = len(classes) if isinstance(classes, list) else 1
    if n_parts > 1:      # a mixed-use project: every system must serve a building part that exists
        missing = [f"system {s['id']}: choose the building part it serves (this project has {n_parts})"
                   for s in data.get("systems", []) if s.get("part") is None]
        outside = [f"system {s['id']}: building part {s['part']} does not exist (parts are 0 to {n_parts - 1})"
                   for s in data.get("systems", []) if s.get("part") is not None and not 0 <= s["part"] < n_parts]
        if missing or outside:
            return _refuse("gate1_required", missing + outside)
    subjects = [
        Subject(s["id"], list(s.get("rules", [])), {
            i["name"]: InputValue(i["value"], i.get("unit"), Provenance(i["provenance"]),
                                  confirmed=i.get("confirmed_by") is not None)
            for i in s.get("inputs", []) if i["name"] != BUILDING_PART}, part=s.get("part"))
        for s in data.get("systems", [])]
    try:
        report = run(RunRequest(_project(data["project"], revision_id, user.firm_id), subjects), pack, ledger=ledger)
    except RunRefused as exc:
        return _refuse(exc.code, exc.reasons)
    save = getattr(repo, "save_results", None)
    if save is None:
        return {"run_id": str(uuid.uuid4()), "report": report}
    try:
        run_id = save(revision_id, user.firm_id, report, data.get("inputs_hash"))
    except RevisionFrozenError:
        raise _err(409, "revision_frozen", "revision is frozen") from None
    except InputsChangedError:
        raise _err(409, "inputs_changed", "an input changed while the rules were running: run them again") from None
    extras = getattr(repo, "run_extras", None)
    return {"run_id": run_id, "report": report, **({} if extras is None else extras(revision_id, user.firm_id))}
