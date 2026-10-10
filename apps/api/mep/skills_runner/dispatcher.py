"""The drafting worker's dispatcher: takes queued jobs and runs each in its own isolated container (see sandbox_docker), then writes the outcome back.

    MEP_DB_URL=<service connection> MEP_WORKER_IMAGE=<skill-worker image> python -m mep.skills_runner.dispatcher

It holds the database credential; the containers it starts hold nothing and have no network. A job is run by `runner.run_skill` with a
DockerExecutor, so the validator-passes-twice rule is the same as everywhere. Run it where a Docker-compatible runtime exists.
"""
import contextlib
import json
import os
import socket
import sys
import threading
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row

from mep.skills_runner.jobs import result_to_json
from mep.skills_runner.runner import Executor, SkillRunResult, SkillUnavailable, run_skill
from mep.skills_runner.sandbox_docker import DockerExecutor

STALE_RUNNING_SECONDS = 900
KEEP_FINISHED_SECONDS = 3600


def claim(conn: psycopg.Connection[dict[str, Any]], who: str) -> dict[str, Any] | None:
    return conn.execute(
        "update skill_job set status = 'running', started_at = now(), locked_by = %s where id = ("
        " select id from skill_job where status = 'queued' order by created_at for update skip locked limit 1)"
        " returning id, firm_id, skill, spec", (who,)).fetchone()


def process(conn: psycopg.Connection[dict[str, Any]], job: dict[str, Any], executor: Executor) -> None:
    try:
        result = run_skill(job["skill"], dict(job["spec"]), executor=executor)
    except SkillUnavailable as exc:
        conn.execute("update skill_job set status = 'failed', finished_at = now(), error = %s where id = %s", (str(exc)[:300], job["id"]))
        return
    except Exception as exc:  # noqa: BLE001 - one bad job must not stop the worker; the API is told generically
        conn.execute("update skill_job set status = 'failed', finished_at = now(), error = %s where id = %s",
                     (f"the worker could not run this job ({type(exc).__name__})", job["id"]))
        return
    meta, contents = result_to_json(result if isinstance(result, SkillRunResult) else SkillRunResult("build_failed"))
    try:
        with conn.transaction():
            for name, data in contents.items():
                conn.execute("insert into skill_job_file (job_id, firm_id, name, content) values (%s, %s, %s, %s)", (job["id"], job["firm_id"], name, data))
            conn.execute("update skill_job set status = 'done', finished_at = now(), result = %s::jsonb where id = %s and status = 'running'",
                         (json.dumps(meta), job["id"]))
    except psycopg.Error as exc:
        with contextlib.suppress(psycopg.Error):
            conn.execute("update skill_job set status = 'failed', finished_at = now(), error = %s where id = %s and status = 'running'",
                         (f"the result could not be stored ({type(exc).__name__})", job["id"]))


def housekeeping(conn: psycopg.Connection[dict[str, Any]]) -> None:
    conn.execute("update skill_job set status = 'failed', finished_at = now(), error = 'the worker stopped while running this job'"
                 " where status = 'running' and started_at < now() - make_interval(secs => %s)", (STALE_RUNNING_SECONDS,))
    conn.execute("delete from skill_job where status in ('done', 'failed') and finished_at < now() - make_interval(secs => %s)", (KEEP_FINISHED_SECONDS,))


def serve(dsn: str, executor: Executor, *, poll: float = 1.0, once: bool = False, stop: threading.Event | None = None) -> int:
    who = f"{socket.gethostname()}:{os.getpid()}"
    done = 0
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        housekeeping(conn)
        last_clean = time.monotonic()
        while not (stop is not None and stop.is_set()):
            job = claim(conn, who)
            if job is not None:
                process(conn, job, executor)
                done += 1
                if once:
                    return done
                continue
            if once:
                return done
            if time.monotonic() - last_clean > 300:
                housekeeping(conn)
                last_clean = time.monotonic()
            time.sleep(poll)
    return done


def main() -> int:
    dsn, image = os.environ.get("MEP_DB_URL"), os.environ.get("MEP_WORKER_IMAGE")
    if not dsn or not image:
        print("MEP_DB_URL and MEP_WORKER_IMAGE are required", file=sys.stderr)
        return 64
    return serve(dsn, DockerExecutor(image)) and 0


if __name__ == "__main__":
    raise SystemExit(main())
