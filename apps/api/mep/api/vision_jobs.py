"""PDF drawings are read in the background: the upload stores the file and enqueues a `vision_job`; a runner renders the pages (sandbox), asks the
vision model, and writes the evidence. People see the job's status (queued / running / done / failed) in the upload panel.

The result is evidence only (provenance 'extracted'); nothing here creates a space or an input.
"""
import contextlib
import hashlib
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row

from mep.api.schedule import CurrentUser
from mep.api.uploads import UploadRefused
from mep.api.uploads_pg import BUCKET, read_pdf
from mep.ingest.store import AlreadyIngested, store_ingest

router = APIRouter()
MAX_ATTEMPTS = 3
STALE_RUNNING_SECONDS = 900


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="vision jobs are not configured")


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


User = Annotated[CurrentUser, Depends(current_user)]


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


@router.get("/revisions/{revision_id}/vision-jobs")
def vision_jobs(revision_id: UUID, user: User, dsn: Annotated[str, Depends(get_dsn)]) -> list[dict[str, Any]]:
    """The status of every PDF reading job of this revision, newest first."""
    with _as_user(dsn, user) as conn:
        if conn.execute("select 1 from revision where id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone() is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "revision not found"})
        rows = conn.execute("select id, name, sha256, status, error, problems, extractions, created_at, finished_at from vision_job"
                            " where revision_id = %s and firm_id = %s order by created_at desc limit 50", (revision_id, user.firm_id)).fetchall()
    return [{"id": str(r["id"]), "name": r["name"], "sha256": r["sha256"], "status": r["status"], "error": r["error"],
             "problems": r["problems"], "extractions": r["extractions"], "created_at": r["created_at"].isoformat(),
             "finished_at": None if r["finished_at"] is None else r["finished_at"].isoformat()} for r in rows]


def enqueue(svc: psycopg.Connection[Any], *, user: CurrentUser, revision_id: UUID, name: str, sha: str, storage_path: str, data: bytes) -> UUID:
    row = svc.execute("insert into vision_job (firm_id, revision_id, requested_by, name, sha256, storage_path, pdf) values (%s, %s, %s, %s, %s, %s, %s)"
                      " returning id", (user.firm_id, revision_id, user.user_id, name, sha, storage_path, data)).fetchone()
    assert row is not None
    return UUID(str(row[0]))


def claim(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any] | None:
    return conn.execute(
        "update vision_job set status = 'running', started_at = now(), attempts = attempts + 1 where id = ("
        " select id from vision_job where status = 'queued' order by created_at for update skip locked limit 1)"
        " returning id, firm_id, revision_id, name, sha256, storage_path, pdf, attempts").fetchone()


def _finish(conn: psycopg.Connection[dict[str, Any]], job_id: UUID, status: str, *, error: str | None = None, problems: list[str] | None = None,
            ingest_run: str | None = None, extractions: int | None = None) -> None:
    conn.execute("update vision_job set status = %s, error = %s, problems = %s::jsonb, ingest_run = %s, extractions = %s, finished_at = now(), pdf = null"
                 " where id = %s", (status, error, __import__("json").dumps(problems or []), ingest_run, extractions, job_id))


def process(conn: psycopg.Connection[dict[str, Any]], job: dict[str, Any], vision: Any) -> None:
    data = bytes(job["pdf"] or b"")
    if hashlib.sha256(data).hexdigest() != job["sha256"]:
        _finish(conn, job["id"], "failed", error="the stored file does not match its checksum")
        return
    try:
        result = read_pdf(job["name"], data, vision)
    except UploadRefused as exc:
        if exc.code == "busy" and job["attempts"] < MAX_ATTEMPTS:
            conn.execute("update vision_job set status = 'queued' where id = %s", (job["id"],))      # try again shortly
            time.sleep(2)
            return
        _finish(conn, job["id"], "failed", error=str(exc.message)[:300])
        return
    except Exception as exc:  # noqa: BLE001 - one bad job must not stop the runner
        _finish(conn, job["id"], "failed", error=f"the file could not be read ({type(exc).__name__})")
        return
    result.source_name = job["name"]
    result.metadata["storage_path"] = job["storage_path"]
    try:
        stored = store_ingest(conn, firm_id=str(job["firm_id"]), revision_id=str(job["revision_id"]), result=result)
    except AlreadyIngested:
        _finish(conn, job["id"], "failed", error="this file was already read for this revision")
        return
    except psycopg.errors.RaiseException as exc:
        _finish(conn, job["id"], "failed", error="the revision was frozen before the reading finished" if "frozen" in str(exc) else "the reading could not be stored")
        return
    _finish(conn, job["id"], "done", problems=list(result.problems), ingest_run=str(stored["ingest_run"]), extractions=int(stored["extractions"]))


def process_pending(dsn: str, vision: Any, limit: int = 20) -> int:
    """Run queued jobs now (the background runner's loop body; tests call it directly). Returns how many were taken."""
    done = 0
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        while done < limit and (job := claim(conn)) is not None:
            process(conn, job, vision)
            done += 1
    return done


def housekeeping(dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("update vision_job set status = 'failed', error = 'the server stopped while reading this file', finished_at = now(), pdf = null"
                     " where status = 'running' and started_at < now() - make_interval(secs => %s)", (STALE_RUNNING_SECONDS,))


class VisionRunner:
    """A background thread that takes queued jobs. One per API process; jobs are claimed with `for update skip locked`, so replicas share the work."""

    def __init__(self, dsn: str, vision: Any, poll: float = 2.0) -> None:
        self._dsn, self._vision, self._poll = dsn, vision, poll
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="vision-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        with contextlib.suppress(Exception):
            housekeeping(self._dsn)
        while not self._stop.is_set():
            taken = 0
            with contextlib.suppress(Exception):
                taken = process_pending(self._dsn, self._vision, limit=5)
            if taken == 0:
                self._stop.wait(self._poll)


__all__ = ["BUCKET", "VisionRunner", "enqueue", "process_pending", "router"]
