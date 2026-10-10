"""The drafting job queue: the API side (enqueue and wait) and the shared (de)serialisation of a run result.

The API never runs a build in queue mode. It enqueues, polls, and on `done` re-checks what came back (every file's sha256 and size against the
manifest the worker produced) before it treats a run as released. No LLM and no compliance value is involved.
"""
import hashlib
import json
import time
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from mep.skills_runner.runner import RunFile, SkillRunResult, SkillUnavailable

WAIT_SECONDS = 330                 # a little more than the dispatcher's own wall limit for build + re-check
PICKUP_SECONDS = 60                # how long a job may sit queued before we say no worker is running
POLL_SECONDS = 0.5


def result_to_json(result: SkillRunResult) -> tuple[dict[str, Any], dict[str, bytes]]:
    """(metadata for skill_job.result, {file name: bytes}); bytes only for a released run."""
    meta = {"status": result.status, "message": result.message, "validation": result.validation, "manifest": result.manifest,
            "files": [{"name": f.name, "role": f.role, "media_type": f.media_type, "bytes": f.bytes, "sha256": f.sha256} for f in result.files]}
    return meta, {f.name: f.content for f in result.files if f.content is not None}


def result_from_json(meta: dict[str, Any], contents: dict[str, bytes]) -> SkillRunResult:
    """Rebuild a run result; a released run is accepted only if every file is present and matches its declared size and sha256 AND the manifest."""
    files = [RunFile(f["name"], f["role"], f["media_type"], int(f["bytes"]), f["sha256"], contents.get(f["name"])) for f in meta.get("files", [])]
    status, message = str(meta.get("status", "build_failed")), str(meta.get("message", ""))
    if status == "ok":
        listed = {e.get("name"): e.get("sha256") for e in (meta.get("manifest") or {}).get("files", [])}
        for f in files:
            data = f.content
            expected = listed.get(f.name) if f.role != "manifest" else f.sha256
            if data is None or hashlib.sha256(data).hexdigest() != f.sha256 or len(data) != f.bytes or expected != f.sha256:
                return SkillRunResult("revalidation_failed", f"{f.name} did not arrive intact from the worker", validation=meta.get("validation") or {},
                                      files=[RunFile(x.name, x.role, x.media_type, x.bytes, x.sha256) for x in files], manifest=meta.get("manifest"))
    else:
        for f in files:
            f.content = None
    return SkillRunResult(status, message, validation=meta.get("validation") or {}, files=files, manifest=meta.get("manifest"))


def enqueue_and_wait(dsn: str, *, firm_id: UUID, revision_id: UUID, user_id: UUID, skill: str, spec: dict[str, Any],
                     wait_seconds: float = WAIT_SECONDS, pickup_seconds: float = PICKUP_SECONDS) -> SkillRunResult:
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        job = conn.execute("insert into skill_job (firm_id, revision_id, requested_by, skill, spec) values (%s, %s, %s, %s, %s::jsonb) returning id",
                           (firm_id, revision_id, user_id, skill, json.dumps(spec, sort_keys=True))).fetchone()
        assert job is not None
        jid = job["id"]
        start = time.monotonic()
        while True:
            row = conn.execute("select status, result, error from skill_job where id = %s", (jid,)).fetchone()
            assert row is not None
            if row["status"] == "done":
                files = conn.execute("select name, content from skill_job_file where job_id = %s", (jid,)).fetchall()
                conn.execute("delete from skill_job where id = %s", (jid,))
                return result_from_json(dict(row["result"] or {}), {r["name"]: bytes(r["content"]) for r in files})
            if row["status"] == "failed":
                conn.execute("delete from skill_job where id = %s", (jid,))
                raise SkillUnavailable(str(row["error"] or "the drafting worker could not run this job"))
            waited = time.monotonic() - start
            if row["status"] == "queued" and waited > pickup_seconds:
                conn.execute("delete from skill_job where id = %s and status = 'queued'", (jid,))
                raise SkillUnavailable("no drafting worker is running: try again in a minute")
            if waited > wait_seconds:
                raise SkillUnavailable("the drafting worker took too long")
            time.sleep(POLL_SECONDS)
