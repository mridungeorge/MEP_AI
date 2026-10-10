"""The tool layer: the ONLY thing a runtime agent can act through, and the safety boundary of the whole agent feature.

A model is told which tools it has, but that is a hint, not a control. Every call comes here and is checked, on the server, by this code:

1. the tool exists, and the AGENT (designer, adversarial_checker, compliance_risk) is allowed to use it;
2. the signed-in HUMAN the agent works for holds a role that may do what the tool does (an agent never has more rights than its user);
3. the arguments are valid for the tool (unknown fields refused);
4. the revision is the user's firm's, and open where the tool changes anything;
5. the call is recorded (allowed or refused) before the answer goes back.

No tool writes a rule result, a review, a sign-off, a confirmation or a ledger row of its own: agents leave NOTES (draft cards, questions, fix
hypotheses, flags, risks, explanations) that people read, and they can ask the deterministic engine and the validated skills to run, which
refuse exactly as they refuse a person. `explain_result` text is post-filtered to the facts of the one result it is about.
"""
import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mep.agents.filter import check_hypothesis, clean_text, filter_explanation
from mep.skills_runner.registry import ENABLED, get_skill
from mep.skills_runner.shortcut import missing_fields

MAX_CALLS_PER_MINUTE = 60
MAX_OPEN_NOTES = 50
MAX_FIELDS_BYTES = 20_000


class Agent(StrEnum):
    DESIGNER = "designer"
    ADVERSARIAL_CHECKER = "adversarial_checker"
    COMPLIANCE_RISK = "compliance_risk"


READ_TOOLS = ("read_results", "read_result", "read_gate_status", "read_notes")
WRITE_TOOLS = ("fill_spec_card", "ask_clarifying_question", "run_skill", "request_rule_run", "propose_fix_hypothesis", "explain_result",
               "raise_flag", "raise_risk")
PERMISSIONS: dict[Agent, frozenset[str]] = {
    Agent.DESIGNER: frozenset(READ_TOOLS + ("fill_spec_card", "ask_clarifying_question", "run_skill", "request_rule_run",
                                            "propose_fix_hypothesis", "explain_result")),
    Agent.ADVERSARIAL_CHECKER: frozenset(READ_TOOLS + ("raise_flag",)),
    Agent.COMPLIANCE_RISK: frozenset(READ_TOOLS + ("raise_risk",)),
}
ALL_ROLES = frozenset({"designer", "checker", "approver"})
HUMAN_ROLES: dict[str, frozenset[str]] = {
    **dict.fromkeys(READ_TOOLS, ALL_ROLES),
    "fill_spec_card": frozenset({"designer"}), "ask_clarifying_question": frozenset({"designer"}), "run_skill": frozenset({"designer"}),
    "request_rule_run": frozenset({"designer"}), "propose_fix_hypothesis": frozenset({"designer"}),
    "explain_result": ALL_ROLES, "raise_flag": ALL_ROLES, "raise_risk": ALL_ROLES,
}
# tools that change the revision's records (so need an open revision)
OPEN_REVISION_ONLY = frozenset({"fill_spec_card", "run_skill", "request_rule_run"})
# which humans may start which agent
AGENT_HUMANS: dict[Agent, frozenset[str]] = {Agent.DESIGNER: frozenset({"designer"}), Agent.ADVERSARIAL_CHECKER: ALL_ROLES,
                                              Agent.COMPLIANCE_RISK: ALL_ROLES}
Severity = Literal["info", "low", "medium", "high"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_M):
    pass


class ResultRef(_M):
    result_id: UUID


class ReadNotes(_M):
    kind: Literal["spec_card_draft", "clarifying_question", "fix_hypothesis", "flag", "risk", "explanation"] | None = None


class FillSpecCard(_M):
    skill: str
    fields: dict[str, Any]


class AskQuestion(_M):
    question: str = Field(min_length=3, max_length=500)
    skill: str | None = None
    field: str | None = Field(default=None, max_length=120)


class RunSkill(_M):
    skill: str
    use_draft: bool = False
    spec: dict[str, Any] | None = None


class ProposeFix(_M):
    result_id: UUID
    text: str = Field(min_length=10, max_length=1000)


class ExplainResult(_M):
    result_id: UUID
    explanation: str = Field(min_length=3, max_length=4000)


class RaiseFlag(_M):
    result_id: UUID | None = None
    severity: Severity
    text: str = Field(min_length=5, max_length=1500)


class RaiseRisk(_M):
    category: Literal["life_safety", "energy", "coordination", "documentation", "scope", "other"]
    severity: Severity
    text: str = Field(min_length=5, max_length=1500)
    result_id: UUID | None = None


MODELS: dict[str, type[BaseModel]] = {
    "read_results": NoArgs, "read_result": ResultRef, "read_gate_status": NoArgs, "read_notes": ReadNotes, "fill_spec_card": FillSpecCard,
    "ask_clarifying_question": AskQuestion, "run_skill": RunSkill, "request_rule_run": NoArgs, "propose_fix_hypothesis": ProposeFix,
    "explain_result": ExplainResult, "raise_flag": RaiseFlag, "raise_risk": RaiseRisk,
}
DESCRIPTIONS = {
    "read_results": "List the current rule results of this revision (id, subject, rule id, outcome, review class, stale).",
    "read_result": "Read one result in full: outcome, the inputs it used, causes, near-miss, citation.",
    "read_gate_status": "Which gates are signed, how many results are reviewed, whether the revision is frozen.",
    "read_notes": "Read the notes already left on this revision (flags, risks, questions, hypotheses).",
    "fill_spec_card": "Put values into the revision's DRAFT spec card for a drafting skill. Nothing is built; the designer confirms.",
    "ask_clarifying_question": "Ask the designer one plain-language question about a missing or doubtful value.",
    "run_skill": "Build with a drafting skill (duct-fab, space-envelope) from the draft card or a full spec. Files exist only if every validator passes.",
    "request_rule_run": "Ask the deterministic rule engine to run. It refuses, with reasons, unless Gate 1 is complete. You never see or set an outcome.",
    "propose_fix_hypothesis": "Record a fix HYPOTHESIS for a failing result. It is stored as 'Hypothesis: verify' and may not claim compliance.",
    "explain_result": "Explain ONE result in plain words. Your text may cite only that result's rule id and outcome; anything else is removed.",
    "raise_flag": "Raise a flag about something that looks wrong. Flags change nothing; people read them.",
    "raise_risk": "Raise a compliance or delivery risk. Risks change nothing; people read them.",
}


class Denied(Exception):
    """The call is not allowed (wrong agent, wrong human role, closed revision, rate limit)."""


class Refused(Exception):
    """The call is allowed but its content is not acceptable (invalid arguments, unknown result, forbidden wording)."""


@dataclass
class ToolResult:
    ok: bool
    data: Any = None
    error: str | None = None
    denied: bool = False

    def as_json(self) -> str:
        return json.dumps({"ok": self.ok, "denied": self.denied, "error": self.error, "data": self.data}, default=str)


@dataclass
class Context:
    agent: Agent
    role: str                       # the signed-in human's role (their acting role)
    revision_id: UUID


class Backend(Protocol):
    """What the tools need from the outside world. The Postgres implementation scopes every call to the user's firm and revision."""

    def revision_state(self) -> dict[str, Any] | None: ...                      # {"frozen": bool} or None when not the firm's
    def results(self) -> list[dict[str, Any]]: ...
    def result(self, result_id: UUID) -> dict[str, Any] | None: ...
    def gate_status(self) -> dict[str, Any]: ...
    def notes(self, kind: str | None) -> list[dict[str, Any]]: ...
    def add_note(self, agent: str, kind: str, body: str, *, skill: str | None = None, result_id: UUID | None = None,
                 severity: str | None = None, data: dict[str, Any] | None = None) -> str: ...
    def latest_draft(self, skill: str) -> dict[str, Any] | None: ...                # the draft SPEC dict itself, or None
    def open_note_count(self, agent: str) -> int: ...
    def calls_in_last_minute(self, agent: str) -> int: ...
    def record_call(self, agent: str, tool: str, allowed: bool, detail: str, args: dict[str, Any]) -> None: ...
    def run_skill(self, skill: str, spec: dict[str, Any]) -> dict[str, Any]: ...
    def request_rule_run(self) -> dict[str, Any]: ...


def deep_merge(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


_PATH_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class ToolLayer:
    ctx: Context
    backend: Backend
    handlers: dict[str, Callable[[Any], Any]] = field(init=False)

    def __post_init__(self) -> None:
        self.handlers = {name: getattr(self, f"_{name}") for name in MODELS}

    # ------------------------------------------------------------------------------------------------------------------ the gate
    def allowed_tools(self) -> list[str]:
        """The tools to OFFER this agent for this human (a hint to the model; call() enforces it again)."""
        return sorted(t for t in PERMISSIONS[self.ctx.agent] if self.ctx.role in HUMAN_ROLES[t])

    def call(self, tool: str, args: dict[str, Any] | None) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        agent = self.ctx.agent.value
        try:
            if tool not in MODELS:
                raise Denied(f"there is no tool called {tool!r}")
            if tool not in PERMISSIONS[self.ctx.agent]:
                raise Denied(f"the {agent} agent may not use {tool}")
            if self.ctx.role not in HUMAN_ROLES[tool]:
                raise Denied(f"{tool} needs a {' or '.join(sorted(HUMAN_ROLES[tool]))}; the person this agent works for is a {self.ctx.role}")
            state = self.backend.revision_state()
            if state is None:
                raise Denied("that revision is not available to this firm")
            if tool in OPEN_REVISION_ONLY and state.get("frozen"):
                raise Denied("the revision is frozen: nothing about it can change")
            if self.backend.calls_in_last_minute(agent) >= MAX_CALLS_PER_MINUTE:
                raise Denied("too many tool calls in the last minute")
            try:
                model = MODELS[tool].model_validate(args)
            except ValidationError as exc:
                raise Refused("invalid arguments: " + "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3])) from None
            data = self.handlers[tool](model)
        except Denied as exc:
            self._record(tool, False, str(exc), args)
            return ToolResult(False, error=str(exc), denied=True)
        except Refused as exc:
            self._record(tool, True, f"refused: {exc}", args)
            return ToolResult(False, error=str(exc))
        self._record(tool, True, "ok", args)
        return ToolResult(True, data=data)

    def _record(self, tool: str, allowed: bool, detail: str, args: dict[str, Any]) -> None:
        trimmed = json.loads(json.dumps(args, default=str)[:2000]) if len(json.dumps(args, default=str)) <= 2000 else {"truncated": True}
        self.backend.record_call(self.ctx.agent.value, tool, allowed, detail[:300], trimmed)

    # ------------------------------------------------------------------------------------------------------------------ helpers
    def _result(self, result_id: UUID) -> dict[str, Any]:
        found = self.backend.result(result_id)
        if found is None:
            raise Refused("no such current result in this revision")
        return found

    def _note(self, kind: str, body: str, **kw: Any) -> str:
        agent = self.ctx.agent.value
        if self.backend.open_note_count(agent) >= MAX_OPEN_NOTES:
            raise Denied(f"{MAX_OPEN_NOTES} notes from this agent are still open: people need to read them first")
        return self.backend.add_note(agent, kind, body, **kw)

    # ------------------------------------------------------------------------------------------------------------------ reads
    def _read_results(self, _: NoArgs) -> Any:
        return [{k: r[k] for k in ("id", "subject_id", "rule_id", "outcome", "review_class", "stale")} for r in self.backend.results()]

    def _read_result(self, a: ResultRef) -> Any:
        return self._result(a.result_id)

    def _read_gate_status(self, _: NoArgs) -> Any:
        return self.backend.gate_status()

    def _read_notes(self, a: ReadNotes) -> Any:
        return self.backend.notes(a.kind)

    # ------------------------------------------------------------------------------------------------------------------ designer
    def _skill_name(self, name: str) -> str:
        if name not in ENABLED:
            raise Refused(f"unknown skill {name!r}; the skills are {', '.join(ENABLED)}")
        return name

    def _fill_spec_card(self, a: FillSpecCard) -> Any:
        skill = self._skill_name(a.skill)
        if len(json.dumps(a.fields, default=str)) > MAX_FIELDS_BYTES:
            raise Refused("that is too much to put in a card")
        schema = get_skill(skill).schema
        known = set(schema.get("properties", {}))
        fixed = {k for k, v in schema.get("properties", {}).items() if "const" in v}
        bad = sorted(k for k in a.fields if k not in known or k in fixed or not _PATH_OK.match(k))
        if bad:
            raise Refused("these are not fields of the card (or are fixed by it): " + ", ".join(bad))
        merged = deep_merge((self.backend.latest_draft(skill) or {}), a.fields)
        missing = missing_fields(skill, merged)
        note = self._note("spec_card_draft", clean_text(f"Draft card for {skill}", 200), skill=skill, data={"spec": merged})
        return {"note_id": note, "draft": merged, "missing": missing, "next": "the designer reviews and confirms the card before anything is built"}

    def _ask_clarifying_question(self, a: AskQuestion) -> Any:
        skill = self._skill_name(a.skill) if a.skill else None
        return {"note_id": self._note("clarifying_question", clean_text(a.question, 500), skill=skill, data={"field": a.field})}

    def _run_skill(self, a: RunSkill) -> Any:
        skill = self._skill_name(a.skill)
        if (a.spec is None) == (not a.use_draft):
            raise Refused("give either a full spec, or set use_draft to build from the draft card (not both, not neither)")
        spec = a.spec if a.spec is not None else self.backend.latest_draft(skill)
        if not spec:
            raise Refused("there is no draft card for this skill yet: fill_spec_card first")
        return self.backend.run_skill(skill, spec)

    def _request_rule_run(self, _: NoArgs) -> Any:
        return self.backend.request_rule_run()

    def _propose_fix_hypothesis(self, a: ProposeFix) -> Any:
        res = self._result(a.result_id)
        if res["outcome"] not in ("FAIL", "NEEDS_JUDGEMENT"):
            raise Refused("a fix hypothesis is only for a result that failed or needs judgement")
        text, why = check_hypothesis(a.text, res["rule_id"])
        if why:
            raise Refused(why)
        body = f"Hypothesis: verify. {text}"
        return {"note_id": self._note("fix_hypothesis", body, result_id=a.result_id, data={"rule_id": res["rule_id"]}),
                "stored_as": body, "label": "Hypothesis: verify"}

    def _explain_result(self, a: ExplainResult) -> Any:
        res = self._result(a.result_id)
        filtered = filter_explanation(a.explanation, res["rule_id"], res["outcome"])
        facts = {"rule_id": res["rule_id"], "outcome": res["outcome"], "document": (res.get("citation") or {}).get("document"),
                 "clause": (res.get("citation") or {}).get("clause"), "rule_status": (res.get("citation") or {}).get("rule_status")}
        note = self._note("explanation", filtered.text or "(nothing left after filtering)", result_id=a.result_id,
                          data={"facts": facts, "redactions": filtered.redactions})
        return {"note_id": note, "text": filtered.text, "redactions": filtered.redactions, "facts": facts}

    # ------------------------------------------------------------------------------------------------------------------ checker and risk
    def _raise_flag(self, a: RaiseFlag) -> Any:
        if a.result_id is not None:
            self._result(a.result_id)
        return {"note_id": self._note("flag", clean_text(a.text, 1500), result_id=a.result_id, severity=a.severity)}

    def _raise_risk(self, a: RaiseRisk) -> Any:
        if a.result_id is not None:
            self._result(a.result_id)
        return {"note_id": self._note("risk", clean_text(a.text, 1500), result_id=a.result_id, severity=a.severity,
                                      data={"category": a.category})}


def tool_catalogue(layer: ToolLayer) -> list[dict[str, Any]]:
    """Name, description and JSON schema of the tools this agent/human pair may use (what the model is shown)."""
    return [{"name": t, "description": DESCRIPTIONS[t], "input_schema": MODELS[t].model_json_schema()} for t in layer.allowed_tools()]


__all__ = ["AGENT_HUMANS", "HUMAN_ROLES", "PERMISSIONS", "Agent", "Backend", "Context", "ToolLayer", "ToolResult", "tool_catalogue"]
