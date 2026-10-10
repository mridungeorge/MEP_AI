"""An in-memory backend for the tool layer: no database. Records what the layer asked of it so tests can prove what did NOT happen."""
from typing import Any
from uuid import UUID, uuid4

RESULT_ID = UUID("11111111-1111-1111-1111-111111111111")
PASS_ID = UUID("22222222-2222-2222-2222-222222222222")
OTHER_FIRM_ID = UUID("33333333-3333-3333-3333-333333333333")


class FakeBackend:
    def __init__(self, frozen: bool = False, revision_exists: bool = True) -> None:
        self.frozen, self.revision_exists = frozen, revision_exists
        self.notes_added: list[dict[str, Any]] = []
        self.calls: list[tuple[str, bool, str]] = []
        self.skill_runs: list[tuple[str, dict[str, Any]]] = []
        self.rule_runs = 0
        self.drafts: dict[str, dict[str, Any]] = {}
        self.minute_calls = 0
        self._results = {
            RESULT_ID: {"id": str(RESULT_ID), "subject_id": "ahu-1", "rule_id": "NCC2025-J6D3-econ-cycle", "outcome": "FAIL", "review_class": "fail",
                        "stale": False, "citation": {"document": "NCC 2025 Volume One", "clause": "J6D3", "rule_status": "draft"},
                        "inputs_used": {"max_airside_component_airflow": {"value": 1500}}, "causes": [], "near_miss": None},
            PASS_ID: {"id": str(PASS_ID), "subject_id": "ahu-1", "rule_id": "NCC2025-J6D3-deadband", "outcome": "PASS", "review_class": "clean_pass",
                      "stale": False, "citation": {"document": "NCC 2025 Volume One", "clause": "J6D3", "rule_status": "draft"},
                      "inputs_used": {}, "causes": [], "near_miss": None},
        }

    def revision_state(self) -> dict[str, Any] | None:
        return {"frozen": self.frozen} if self.revision_exists else None

    def results(self) -> list[dict[str, Any]]:
        return list(self._results.values())

    def result(self, result_id: UUID) -> dict[str, Any] | None:
        return self._results.get(result_id)

    def gate_status(self) -> dict[str, Any]:
        return {"signed_gates": ["gate1"], "results": 2, "approved": 0, "frozen": self.frozen}

    def notes(self, kind: str | None) -> list[dict[str, Any]]:
        return [n for n in self.notes_added if kind is None or n["kind"] == kind]

    def add_note(self, agent: str, kind: str, body: str, *, skill: str | None = None, result_id: UUID | None = None,
                 severity: str | None = None, data: dict[str, Any] | None = None) -> str:
        nid = str(uuid4())
        self.notes_added.append({"id": nid, "agent": agent, "kind": kind, "body": body, "skill": skill, "result_id": result_id,
                                 "severity": severity, "data": data or {}})
        if kind == "spec_card_draft" and skill:
            self.drafts[skill] = (data or {})["spec"]
        return nid

    def latest_draft(self, skill: str) -> dict[str, Any] | None:
        return self.drafts.get(skill)

    def open_note_count(self, agent: str) -> int:
        return sum(1 for n in self.notes_added if n["agent"] == agent)

    def calls_in_last_minute(self, agent: str) -> int:
        return self.minute_calls

    def record_call(self, agent: str, tool: str, allowed: bool, detail: str, args: dict[str, Any]) -> None:
        self.calls.append((tool, allowed, detail))

    def run_skill(self, skill: str, spec: dict[str, Any]) -> dict[str, Any]:
        self.skill_runs.append((skill, spec))
        return {"status": "ok", "released": True, "files": []}

    def request_rule_run(self) -> dict[str, Any]:
        self.rule_runs += 1
        return {"status": "refused", "code": "gate1_required"}
