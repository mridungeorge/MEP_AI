"""Drafting-skill runs on Postgres: who may run, the firm defaults, the record of each run, and the released files.

Writes go through the service connection (a client may never write an artifact); reads run as the signed-in user, so row-level security
decides what is listed and the artifact_blob policy releases a file only when its artifact is `released` (validator passed).
"""
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.api.schedule import CurrentUser
from mep.skills_runner.firm_sheet import FirmTemplates, apply_to_result
from mep.skills_runner.jobs import enqueue_and_wait
from mep.skills_runner.runner import SkillRunResult, SkillUnavailable, run_skill


class SkillsRefused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def spec_sha(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PgSkills:
    def __init__(self, dsn: str, user: CurrentUser, executor: str | None = None) -> None:
        self._dsn, self._user, self._executor = dsn, user, executor

    @contextmanager
    def _as_user(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)", (self._user.claims_json(),))
            yield conn

    def _service(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row)

    # ---- running ------------------------------------------------------------------------------------------------------
    def execute(self, revision_id: UUID, skill: str, spec: dict[str, Any]) -> SkillRunResult:
        """MEP_SKILL_EXECUTOR=queue: the build runs in the separate drafting worker (a job in the queue). Otherwise in a child process here."""
        mode = self._executor or os.environ.get("MEP_SKILL_EXECUTOR", "local")
        if mode == "disabled":
            raise SkillUnavailable("drafting is switched off on this server: set MEP_SKILL_EXECUTOR=queue and run the drafting worker")
        if mode not in ("local", "queue"):
            raise SkillUnavailable(f"MEP_SKILL_EXECUTOR={mode!r} is not understood (use queue)")
        if mode == "queue":
            result = enqueue_and_wait(self._dsn, firm_id=self._user.firm_id, revision_id=revision_id, user_id=self._user.user_id, skill=skill, spec=spec)
        else:
            result = run_skill(skill, spec, attachments=self._attachments(revision_id, spec))
        return apply_to_result(result, skill, spec, self._firm_templates()) if result.released else result

    def _firm_templates(self) -> FirmTemplates | None:
        """The firm's latest title block and layer standard (uploaded by an administrator); None when it has neither."""
        with self._service() as conn:
            rows = conn.execute("select distinct on (kind) kind, name, content from firm_template where firm_id = %s order by kind, created_at desc",
                                (self._user.firm_id,)).fetchall()
        out = FirmTemplates()
        for r in rows:
            if r["kind"] == "title_block":
                out.title_block, out.title_block_name = bytes(r["content"]), r["name"]
            else:
                try:
                    out.layer_standard, out.layer_standard_name = json.loads(bytes(r["content"]).decode("utf-8")), r["name"]
                except ValueError:
                    continue
        return out if out.any else None

    def _attachments(self, revision_id: UUID, spec: dict[str, Any]) -> dict[str, bytes] | None:
        """The architect's model for a skill that is given one (ifc-mep): looked up by the checksum in the card, within this revision and firm."""
        digest = spec.get("base_ifc_sha256")
        if not isinstance(digest, str):
            return None
        with self._service() as conn:
            row = conn.execute("select content from base_model where revision_id = %s and firm_id = %s and file_sha256 = %s",
                               (revision_id, self._user.firm_id, digest)).fetchone()
        return {"base.ifc": bytes(row["content"])} if row else None

    def server_fill(self, skill: str, revision_id: UUID, spec: dict[str, Any]) -> dict[str, Any]:
        """Facts that must come from this revision, not from the card: hvac-dxf's sizing schedule. A card carrying a different copy is refused."""
        if skill != "hvac-dxf":
            return spec
        from mep.api.sizing_api import schedule_entries
        schedule = schedule_entries(self._dsn, self._user, revision_id)
        if not schedule:
            raise SkillsRefused(409, "no_sizing_schedule", "this revision has no sound sizing schedule to draw from: add airflows to the services schedule first")
        given = spec.get("sizing_schedule")
        if given is not None and (not isinstance(given, list) or sorted(given, key=lambda e: str(e.get("tag")) if isinstance(e, dict) else "") != schedule):
            raise SkillsRefused(409, "schedule_mismatch", "the card's sizing schedule differs from this revision's sizing schedule (leave it out and the server fills it in)")
        return {**spec, "sizing_schedule": schedule}

    # ---- confirmed card versions ---------------------------------------------------------------------------------------
    def confirm_card(self, revision_id: UUID, skill: str, digest: str, note_id: UUID | None) -> None:
        try:
            with self._as_user() as conn:
                conn.execute("select spec_card_confirm(%s, %s, %s, %s)", (revision_id, skill, digest, note_id))
        except psycopg.errors.InsufficientPrivilege as exc:
            raise SkillsRefused(403, "forbidden", str(exc).splitlines()[0]) from None

    def card_confirmed(self, revision_id: UUID, skill: str, digest: str) -> bool:
        with self._as_user() as conn:
            return conn.execute("select 1 from spec_confirmation where revision_id = %s and firm_id = %s and skill = %s and spec_sha256 = %s",
                                (revision_id, self._user.firm_id, skill, digest)).fetchone() is not None

    # ---- firm defaults ----------------------------------------------------------------------------------------------
    def firm_defaults(self, skill: str) -> dict[str, Any]:
        with self._as_user() as conn:
            row = conn.execute("select defaults from firm_skill_defaults where firm_id = %s and skill = %s", (self._user.firm_id, skill)).fetchone()
        return {} if row is None else dict(row["defaults"])

    def set_firm_defaults(self, skill: str, defaults: dict[str, Any]) -> None:
        with self._service() as conn:
            conn.execute(
                "insert into firm_skill_defaults (firm_id, skill, defaults, updated_by) values (%s, %s, %s::jsonb, %s)"
                " on conflict (firm_id, skill) do update set defaults = excluded.defaults, updated_by = excluded.updated_by, updated_at = now()",
                (self._user.firm_id, skill, json.dumps(defaults), self._user.user_id))

    # ---- runs -------------------------------------------------------------------------------------------------------
    def revision_frozen(self, revision_id: UUID) -> bool | None:
        with self._as_user() as conn:
            row = conn.execute("select frozen_at is not null as frozen from revision where id = %s and firm_id = %s",
                               (revision_id, self._user.firm_id)).fetchone()
        return None if row is None else bool(row["frozen"])

    def record(self, revision_id: UUID, skill: str, spec: dict[str, Any], result: SkillRunResult, via: str = "user") -> dict[str, Any]:
        """Store the run. Files are stored (and released) ONLY for a run that passed both validators; a run that produced files the
        validator rejected leaves unreleased artifact rows with no bytes, so there is a record and nothing to download."""
        with self._service() as conn:
            run_id = conn.execute(
                "insert into skill_run (firm_id, revision_id, skill, spec, spec_sha256, status, message, validation, requested_by, requested_via)"
                " values (%s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s, %s) returning id",
                (self._user.firm_id, revision_id, skill, json.dumps(spec), spec_sha(spec), result.status, result.message[:300],
                 json.dumps(result.validation), self._user.user_id, via)).fetchone()["id"]
            artifacts = []
            for f in result.files:
                released = result.released
                art = conn.execute(
                    "insert into artifact (firm_id, revision_id, kind, path, checksum, validator, released, run_id, name, media_type)"
                    " values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s) returning id",
                    (self._user.firm_id, revision_id, f.role, f"skills/{skill}/{run_id}/{f.name}", f.sha256,
                     json.dumps({"passed": released, "skill": skill, "failed": result.validation.get("failed", []),
                                 "checks": len(result.validation.get("checks", []))}), released, run_id, f.name, f.media_type)).fetchone()["id"]
                if released and f.content is not None:
                    conn.execute("insert into artifact_blob (artifact_id, firm_id, content, media_type) values (%s, %s, %s, %s)",
                                 (art, self._user.firm_id, f.content, f.media_type))
                artifacts.append({"artifact_id": str(art), "name": f.name, "role": f.role, "bytes": f.bytes, "sha256": f.sha256,
                                  "released": released})
        return {"run_id": str(run_id), "artifacts": artifacts}

    def runs(self, revision_id: UUID) -> list[dict[str, Any]]:
        with self._as_user() as conn:
            rows = conn.execute(
                "select r.id, r.skill, r.status, r.message, r.validation, r.created_at, r.requested_via, r.spec_sha256,"
                " coalesce((select jsonb_agg(jsonb_build_object('artifact_id', a.id, 'name', a.name, 'role', a.kind, 'released', a.released)"
                " order by a.name) from artifact a where a.run_id = r.id), '[]') as artifacts"
                " from skill_run r where r.revision_id = %s and r.firm_id = %s order by r.created_at desc limit 100",
                (revision_id, self._user.firm_id)).fetchall()
        return [{"run_id": str(r["id"]), "skill": r["skill"], "status": r["status"], "message": r["message"],
                 "passed": r["status"] == "ok", "validation": r["validation"], "created_at": r["created_at"].isoformat(),
                 "via": r["requested_via"], "spec_sha256": r["spec_sha256"], "artifacts": r["artifacts"]} for r in rows]

    def spec_of(self, revision_id: UUID, run_id: UUID) -> tuple[str, dict[str, Any]] | None:
        with self._as_user() as conn:
            row = conn.execute("select skill, spec from skill_run where id = %s and revision_id = %s and firm_id = %s",
                               (run_id, revision_id, self._user.firm_id)).fetchone()
        return None if row is None else (row["skill"], dict(row["spec"]))

    def download(self, artifact_id: UUID) -> tuple[str, str, bytes] | None:
        """(name, media type, bytes) of a RELEASED artifact of the user's firm; None for anything else."""
        with self._as_user() as conn:
            row = conn.execute("select a.name, b.media_type, b.content from artifact a join artifact_blob b on b.artifact_id = a.id"
                               " where a.id = %s and a.firm_id = %s and a.released", (artifact_id, self._user.firm_id)).fetchone()
        return None if row is None else (row["name"], row["media_type"], bytes(row["content"]))

    def released_file(self, revision_id: UUID, run_id: UUID, role: str) -> tuple[str, bytes] | None:
        with self._as_user() as conn:
            row = conn.execute("select a.name, b.content from artifact a join artifact_blob b on b.artifact_id = a.id where a.run_id = %s"
                               " and a.revision_id = %s and a.firm_id = %s and a.released and a.kind = %s",
                               (run_id, revision_id, self._user.firm_id, role)).fetchone()
        return None if row is None else (row["name"], bytes(row["content"]))
