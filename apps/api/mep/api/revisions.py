"""Revision endpoints: lineage, stored results, the diff against the parent (with reasoning traces and cross-rule conflicts), the
designer's confirmation of that diff, and freezing.

No compliance is decided here. The diff, the stale set and the traces come from `mep.diff` (the rules' own dependency graph and the
stored results); the conflicts come from the rule engine's own evaluation (`mep.engine.cross_rule`).
"""
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from mep.api.gate1 import ConfirmRefused, _project
from mep.api.schedule import CurrentUser, RevisionFrozenError
from mep.diff.graph import DependencyGraph
from mep.diff.service import build_diff
from mep.engine.cross_rule import cross_rule_rerun
from mep.engine.loader import RulePack
from mep.engine.model import InputValue, Provenance, Subject

router = APIRouter()
DESIGNER_ROLES = frozenset({"designer"})


class RevisionsRepository(Protocol):
    def revision_info(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None: ...
    def lineage(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None: ...
    def current_results(self, revision_id: UUID, firm_id: UUID) -> list[dict[str, Any]]: ...
    def diff_data(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any] | None: ...
    def confirm_diff(self, revision_id: UUID, diff_hash: str) -> None: ...
    def freeze(self, revision_id: UUID) -> None: ...
    def load_run_inputs(self, revision_id: UUID, firm_id: UUID) -> dict[str, Any]: ...


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_repository() -> RevisionsRepository:
    raise HTTPException(status_code=503, detail="repository is not configured")


def get_graph() -> DependencyGraph:
    raise HTTPException(status_code=503, detail="rule pack is not configured")


def get_pack() -> RulePack:
    raise HTTPException(status_code=503, detail="rule pack is not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Repo = Annotated[RevisionsRepository, Depends(get_repository)]
Graph = Annotated[DependencyGraph, Depends(get_graph)]
Pack = Annotated[RulePack, Depends(get_pack)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _subjects(repo: RevisionsRepository, revision_id: UUID, firm_id: UUID) -> tuple[dict[str, Subject], Any]:
    data = repo.load_run_inputs(revision_id, firm_id)
    subjects = {s["id"]: Subject(s["id"], list(s.get("rules", [])), {
        i["name"]: InputValue(i["value"], i.get("unit"), Provenance(i["provenance"]), confirmed=i.get("confirmed_by") is not None)
        for i in s.get("inputs", []) if i["name"] != "building_part"}, part=s.get("part")) for s in data.get("systems", [])}
    return subjects, data


def conflicts_of(repo: RevisionsRepository, pack: RulePack, graph: DependencyGraph, firm_id: UUID, revision_id: UUID,
                 parent_id: UUID, input_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each system whose inputs changed since the parent: re-run every dependent rule with the child's values; a change that helps
    one rule and breaks another is a conflict naming both rules and the input."""
    changed: dict[str, list[str]] = {}
    for i in input_items:
        if i["change"] == "changed" and not i["is_part_change"]:
            changed.setdefault(i["system"], []).append(i["name"])
    if not changed:
        return []
    out: list[dict[str, Any]] = []
    try:
        parent_subjects, parent_data = _subjects(repo, parent_id, firm_id)
        child_subjects, _ = _subjects(repo, revision_id, firm_id)
        project = _project(parent_data["project"], parent_id, firm_id)
        for tag, names in sorted(changed.items()):
            base, now = parent_subjects.get(tag), child_subjects.get(tag)
            if base is None or now is None:
                continue
            updates = {n: now.inputs[n] for n in names if n in now.inputs and now.inputs[n].confirmed}
            if not updates:
                continue
            result = cross_rule_rerun(subject=base, project=project, changes=updates, pack=pack, graph=graph)
            out += [{"subject_id": c.subject_id, "input": c.input_name, "better_rule": c.better.rule_id,
                     "worse_rule": c.worse.rule_id, "text": c.describe()} for c in result.conflicts]
    except (KeyError, ValueError, TypeError):    # a half-entered project cannot be re-run: the run itself will say what is missing
        return out
    return out


@router.get("/revisions/{revision_id}/lineage")
def get_lineage(revision_id: UUID, user: User, repo: Repo) -> dict[str, Any]:
    found = repo.lineage(revision_id, user.firm_id)
    if found is None:
        raise _err(404, "not_found", "revision not found")
    return found


@router.get("/revisions/{revision_id}/results")
def get_results(revision_id: UUID, user: User, repo: Repo, graph: Graph) -> dict[str, Any]:
    """The revision's own current results; before its first run, the parent's results carried over with the ones the diff makes stale."""
    info = repo.revision_info(revision_id, user.firm_id)
    if info is None:
        raise _err(404, "not_found", "revision not found")
    own = repo.current_results(revision_id, user.firm_id)
    if own or info["parent_revision_id"] is None:
        return {"source": "own", "results": own}
    data = repo.diff_data(revision_id, user.firm_id)
    stale = {(s["subject_id"], s["rule_id"]) for s in build_diff(data, graph)["stale"]} if data else set()
    return {"source": "carried_from_parent", "parent_revision_id": info["parent_revision_id"],
            "results": [{**r, "stale": (r["subject_id"], r["rule_id"]) in stale} for r in (data or {}).get("parent_results", [])]}


@router.get("/revisions/{revision_id}/diff")
def get_diff(revision_id: UUID, user: User, repo: Repo, graph: Graph, pack: Pack) -> dict[str, Any]:
    data = repo.diff_data(revision_id, user.firm_id)
    if data is None:
        raise _err(404, "not_found", "revision not found")
    diff = build_diff(data, graph)
    diff["conflicts"] = [] if data["parent"] is None else conflicts_of(
        repo, pack, graph, user.firm_id, revision_id, UUID(data["parent"]["id"]), diff["inputs"])
    return diff


@router.post("/revisions/{revision_id}/diff/confirm")
def confirm_diff(revision_id: UUID, user: User, repo: Repo, graph: Graph) -> dict[str, Any]:
    """Confirm the diff AS IT STANDS: every changed or added space must already be confirmed (Gate 1, changed values only)."""
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer confirms a revision diff")
    data = repo.diff_data(revision_id, user.firm_id)
    if data is None:
        raise _err(404, "not_found", "revision not found")
    if data["revision"]["frozen"]:
        raise _err(409, "revision_frozen", "revision is frozen")
    if data["parent"] is None:
        raise _err(409, "no_parent", "this revision has no parent: there is no diff to confirm")
    diff = build_diff(data, graph)
    if diff["needs_confirmation"]:
        raise _err(409, "gate1_required", f"{len(diff['needs_confirmation'])} changed or added space(s) are not confirmed at Gate 1")
    try:
        repo.confirm_diff(revision_id, diff["hash"])
    except ConfirmRefused as exc:
        raise _err(409, "cannot_confirm", str(exc)) from None
    except RevisionFrozenError:
        raise _err(409, "revision_frozen", "revision is frozen") from None
    return {"confirmed": True, "hash": diff["hash"]}


@router.post("/revisions/{revision_id}/freeze")
def freeze(revision_id: UUID, user: User, repo: Repo, graph: Graph) -> dict[str, Any]:
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer freezes a revision")
    data = repo.diff_data(revision_id, user.firm_id)
    if data is None:
        raise _err(404, "not_found", "revision not found")
    if data["parent"] is not None and not build_diff(data, graph)["confirmed"]:
        raise _err(409, "diff_not_confirmed", "confirm the revision diff before freezing")
    try:
        repo.freeze(revision_id)
    except ConfirmRefused as exc:
        raise _err(409, "cannot_freeze", str(exc)) from None
    return {"frozen": True}
