"""Drafting-skill runs on Postgres: who may run, the firm defaults, the record of each run, and the released files.

Writes go through the service connection (a client may never write an artifact); reads run as the signed-in user, so row-level security
decides what is listed and the artifact_blob policy releases a file only when its artifact is `released` (validator passed).
"""
import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.api.schedule import CurrentUser
from mep.skills_runner.runner import SkillRunResult


class SkillsRefused(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def spec_sha(spec: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PgSkills:
    def __init__(self, dsn: str, user: CurrentUser) -> None:
        self._dsn, self._user = dsn, user

    @contextmanager
    def _as_user(self) -> Iterator[psycopg.Connection[dict[str, Any]]]:
        with psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)", (self._user.claims_json(),))
            yield conn

    def _service(self) -> psycopg.Connection[dict[str, Any]]:
        return psycopg.connect(self._dsn, autocommit=False, row_factory=dict_row)

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
