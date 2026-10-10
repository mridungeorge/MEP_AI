"""The Postgres side of the agent tool layer, for ONE signed-in user and ONE revision.

Reads run as the user (row-level security). The only writes are notes and the call log (service connection, two small tables), plus the two
doors that already refuse for people: the validated drafting skills and the deterministic rule run. Nothing here can write a result,
a review, a sign-off or a confirmation.
"""
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row

from mep.api import gate1
from mep.api.pg import PgLedger, PgRepository
from mep.api.schedule import CurrentUser
from mep.api.skills import card_digest, effective_spec, perform_run
from mep.api.skills_pg import PgSkills, SkillsRefused
from mep.engine.loader import RulePack


class PgAgentBackend:
    def __init__(self, dsn: str, user: CurrentUser, revision_id: UUID, pack: RulePack | None = None) -> None:
        self._dsn, self._user, self._rev, self._pack = dsn, user, revision_id, pack

    @contextmanager
    def _as_user(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)", (self._user.claims_json(),))
            yield conn

    def _service(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row)

    # ---- reads --------------------------------------------------------------------------------------------------------
    def revision_state(self) -> dict[str, Any] | None:
        with self._as_user() as conn:
            r = conn.execute("select frozen_at is not null as frozen, architect_rev from revision where id = %s and firm_id = %s",
                             (self._rev, self._user.firm_id)).fetchone()
        return None if r is None else {"frozen": bool(r["frozen"]), "architect_rev": r["architect_rev"]}

    def results(self) -> list[dict[str, Any]]:
        with self._as_user() as conn:
            rows = conn.execute("select id, subject_id, rule_id, result::text as outcome, review_class, stale from rule_result"
                                " where revision_id = %s and firm_id = %s and current order by subject_id, rule_id", (self._rev, self._user.firm_id)).fetchall()
        return [{**r, "id": str(r["id"])} for r in rows]

    def result(self, result_id: UUID) -> dict[str, Any] | None:
        with self._as_user() as conn:
            r = conn.execute("select id, subject_id, rule_id, result::text as outcome, review_class, stale, citation, inputs, causes, near_miss"
                             " from rule_result where id = %s and revision_id = %s and firm_id = %s and current",
                             (result_id, self._rev, self._user.firm_id)).fetchone()
        return None if r is None else {**r, "id": str(r["id"]), "inputs_used": r.pop("inputs")}

    def gate_status(self) -> dict[str, Any]:
        with self._as_user() as conn:
            signed = [r["gate"] for r in conn.execute("select gate::text as gate from signoff where revision_id = %s order by gate", (self._rev,))]
            counts = conn.execute(
                "select count(*) as total, count(v.decision) filter (where v.decision = 'approve') as approved from rule_result rr"
                " left join review_latest v on v.rule_result_id = rr.id where rr.revision_id = %s and rr.firm_id = %s and rr.current",
                (self._rev, self._user.firm_id)).fetchone()
            frozen = conn.execute("select frozen_at is not null as f from revision where id = %s", (self._rev,)).fetchone()
        return {"signed_gates": signed, "results": counts["total"] if counts else 0, "approved": counts["approved"] if counts else 0,
                "frozen": bool(frozen and frozen["f"])}

    def notes(self, kind: str | None) -> list[dict[str, Any]]:
        with self._as_user() as conn:
            rows = conn.execute("select id, agent, kind, skill, severity, body, status, rule_result_id, created_at, post_freeze,"
                                " case when kind = 'spec_card_draft' then data -> 'spec' end as spec from agent_note where revision_id = %s"
                                " and firm_id = %s and (%s::text is null or kind = %s) order by created_at desc limit 100",
                                (self._rev, self._user.firm_id, kind, kind)).fetchall()
        return [{**r, "id": str(r["id"]), "rule_result_id": None if r["rule_result_id"] is None else str(r["rule_result_id"]),
                 "created_at": r["created_at"].isoformat()} for r in rows]

    def latest_draft(self, skill: str) -> dict[str, Any] | None:
        with self._as_user() as conn:
            r = conn.execute("select data from agent_note where revision_id = %s and firm_id = %s and kind = 'spec_card_draft' and skill = %s"
                             " order by created_at desc limit 1", (self._rev, self._user.firm_id, skill)).fetchone()
        return None if r is None else (dict(r["data"]).get("spec") or None)

    def open_note_count(self, agent: str) -> int:
        with self._as_user() as conn:
            return int(conn.execute("select count(*) as n from agent_note where revision_id = %s and agent = %s and status = 'open'",
                                    (self._rev, agent)).fetchone()["n"])

    def calls_in_last_minute(self, agent: str) -> int:
        with self._as_user() as conn:
            return int(conn.execute("select count(*) as n from agent_call where revision_id = %s and agent = %s and created_at > now() - interval '1 minute'",
                                    (self._rev, agent)).fetchone()["n"])

    def resolve_note(self, note_id: UUID, status: str) -> None:
        """A person closes an open note (answered / dismissed). Notes are otherwise append-only."""
        with self._as_user() as conn:
            conn.execute("select agent_note_resolve(%s, %s, %s)", (self._rev, note_id, status))

    # ---- writes: notes and the call log only ----------------------------------------------------------------------------
    def add_note(self, agent: str, kind: str, body: str, *, skill: str | None = None, result_id: UUID | None = None,
                 severity: str | None = None, data: dict[str, Any] | None = None) -> str:
        with self._service() as conn:
            row = conn.execute(
                "insert into agent_note (firm_id, revision_id, agent, kind, skill, rule_result_id, severity, body, data, created_by)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s) returning id",
                (self._user.firm_id, self._rev, agent, kind, skill, result_id, severity, body, json.dumps(data or {}, default=str), self._user.user_id)).fetchone()
        assert row is not None
        return str(row["id"])

    def record_call(self, agent: str, tool: str, allowed: bool, detail: str, args: dict[str, Any]) -> None:
        with self._service() as conn:
            conn.execute("insert into agent_call (firm_id, revision_id, agent, tool, allowed, detail, args, requested_by)"
                         " values (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
                         (self._user.firm_id, self._rev, agent, tool, allowed, detail, json.dumps(args, default=str), self._user.user_id))

    # ---- the two doors that refuse for people exactly as they refuse for an agent ---------------------------------------
    def card_digest(self, skill: str, spec: dict[str, Any]) -> str:
        svc = PgSkills(self._dsn, self._user)
        try:
            return card_digest(effective_spec(svc, skill, spec)[0])
        except Exception:  # noqa: BLE001 - an unknown skill or a card that cannot be completed has no version
            return card_digest(spec)

    def card_confirmed(self, skill: str, digest: str) -> bool:
        return PgSkills(self._dsn, self._user).card_confirmed(self._rev, skill, digest)

    def run_skill(self, skill: str, spec: dict[str, Any], expected_digest: str | None = None) -> dict[str, Any]:
        svc = PgSkills(self._dsn, self._user)
        try:
            result, recorded, filled = perform_run(svc, self._user, self._rev, skill, spec, via="agent", expected_digest=expected_digest)
        except SkillsRefused as exc:
            return {"status": "refused", "code": exc.code, "message": exc.message, "released": False}
        return {"run_id": recorded["run_id"], "status": result.status, "released": result.released, "message": result.message,
                "validator_failed": list(result.validation.get("failed", [])),
                "files": [{"artifact_id": f["artifact_id"], "name": f["name"], "released": f["released"]} for f in recorded["artifacts"]],
                "firm_defaults_applied": filled}

    def request_rule_run(self) -> dict[str, Any]:
        if self._pack is None:
            return {"status": "unavailable", "message": "no rule pack is loaded on this server"}
        repo = PgRepository(self._dsn, self._user, self._pack)
        try:
            out = gate1.run_rules(self._rev, self._user, repo, self._pack, PgLedger(self._dsn))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            return {"status": "refused", "http": exc.status_code, "code": detail.get("code"), "message": detail.get("message")}
        if isinstance(out, JSONResponse):
            body = json.loads(bytes(out.body))
            return {"status": "refused", "http": out.status_code, "code": body.get("code"), "reasons": body.get("reasons", [])[:20]}
        counts: dict[str, int] = {}
        for r in (out.get("report", {}).get("results", []) if isinstance(out, dict) else []):
            counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
        return {"status": "run", "run_id": out.get("run_id"), "counts": counts, "note": "outcomes come from the rule files; read them with read_results"}
