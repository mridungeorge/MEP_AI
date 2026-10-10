"""Runtime agents in the app: ask the drafting assistant, the adversarial checker or the compliance-risk agent about a revision, and read the
notes they leave. The agents act only through the tool layer (mep.agents.tools), as the signed-in person and with no more rights than that person.
"""
import time
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from mep.agents.filter import filter_free_text
from mep.agents.runtime import AgentsNotConfigured, Runtime, SdkRuntime, sdk_ready
from mep.agents.tools import AGENT_HUMANS, Agent, Context, ToolLayer
from mep.api.schedule import CurrentUser

router = APIRouter()


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_runtime() -> Runtime:
    """A model-backed runtime, or a clear 503 when this server has no model access (tests replace this with a scripted one)."""
    try:
        return SdkRuntime()
    except AgentsNotConfigured as exc:
        raise _err(503, "agents_unavailable", str(exc)) from None


def get_backend_factory() -> Any:
    raise HTTPException(status_code=503, detail="agents are not configured")


User = Annotated[CurrentUser, Depends(current_user)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


MESSAGES_PER_WINDOW = 12
WINDOW_SECONDS = 600
_sent: dict[str, list[float]] = {}


def _throttle(user: CurrentUser) -> None:
    """A person's messages to agents are limited (each can spend model budget and holds a worker thread). Per server process."""
    now = time.monotonic()
    key = str(user.user_id)
    recent = [t for t in _sent.get(key, []) if now - t < WINDOW_SECONDS]
    if len(recent) >= MESSAGES_PER_WINDOW:
        raise _err(429, "too_many_requests", "you have asked the agents a lot in the last few minutes; wait a little")
    _sent[key] = [*recent, now]
    if len(_sent) > 5000:
        for k in list(_sent)[:2500]:
            _sent.pop(k, None)


class MessageBody(BaseModel):
    message: Annotated[str, Field(min_length=2, max_length=2000)]


class ResolveBody(BaseModel):
    status: Annotated[str, Field(pattern="^(answered|dismissed)$")]


@router.get("/agents/status")
def status(user: User) -> dict[str, Any]:
    ok, why = sdk_ready()
    return {"configured": ok, "reason": why}


@router.post("/revisions/{revision_id}/agents/{agent}/message")
def message(revision_id: UUID, agent: Agent, body: MessageBody, user: User, factory: Annotated[Any, Depends(get_backend_factory)],
            runtime: Annotated[Runtime, Depends(get_runtime)]) -> dict[str, Any]:
    if user.role not in AGENT_HUMANS[agent]:
        raise _err(403, "forbidden", f"a {user.role} cannot ask the {agent.value} agent")
    backend = factory(user, revision_id)
    if backend.revision_state() is None:
        raise _err(404, "not_found", "revision not found")
    layer = ToolLayer(Context(agent, user.role, revision_id), backend)
    _throttle(user)
    try:
        reply = runtime.converse(layer, body.message)
    except TimeoutError:
        raise _err(504, "agent_timeout", "the agent took too long and was stopped") from None
    # the model's own words are never shown as they came: outcomes and compliance wording come only from the stored results
    shown = filter_free_text(reply.text, {r["rule_id"] for r in backend.results()})
    return {"agent": agent.value, "reply": shown.text, "reply_is": "an assistant's note, not a rule result", "redactions": len(shown.redactions),
            "calls": [{k: c.get(k) for k in ("tool", "ok", "denied", "error")} for c in reply.calls]}


@router.get("/revisions/{revision_id}/agent-notes")
def notes(revision_id: UUID, user: User, factory: Annotated[Any, Depends(get_backend_factory)], kind: str | None = None) -> list[dict[str, Any]]:
    backend = factory(user, revision_id)
    if backend.revision_state() is None:
        raise _err(404, "not_found", "revision not found")
    return backend.notes(kind)  # type: ignore[no-any-return]


@router.post("/revisions/{revision_id}/agent-notes/{note_id}/resolve")
def resolve(revision_id: UUID, note_id: UUID, body: ResolveBody, user: User, factory: Annotated[Any, Depends(get_backend_factory)]) -> dict[str, Any]:
    backend = factory(user, revision_id)
    try:
        backend.resolve_note(note_id, body.status)
    except psycopg.errors.InsufficientPrivilege:
        raise _err(404, "not_found", "no such open note") from None
    return {"resolved": True}
