"""Postgres and Supabase Storage behind the upload endpoint.

Order of work (each step can only fail closed): look the revision up as the user (RLS, firm scope, frozen check) -> refuse a
file already ingested into this revision -> PARSE the file (an unreadable file leaves nothing behind) -> store it in the private
`uploads` bucket as the USER (storage RLS: own firm, own revision, designer only) -> write ingest_run, extraction evidence and
the space rows on a service connection. Spaces are always provenance 'extracted'.
"""
import contextlib
import hashlib
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg

from mep.api.lineage_pg import (
    LABEL_OK,
    DuplicateChild,
    carry_confirmations,
    create_child,
    drop_empty_child,
    ledger_created,
    next_label,
)
from mep.api.pg import health_view
from mep.api.schedule import CurrentUser
from mep.api.uploads import UploadRefused
from mep.ingest.pdf import extract_rendered
from mep.ingest.records import IngestRefused, IngestResult
from mep.ingest.sandbox import read_isolated, render_isolated
from mep.ingest.store import AlreadyIngested, store_ingest

BUCKET = "uploads"
MAX_CONCURRENT_READS = 4
_READERS = threading.BoundedSemaphore(MAX_CONCURRENT_READS)


def read_file(kind: str, name: str, data: bytes) -> IngestResult:
    """Parse the uploaded bytes with the reader for `kind`, in a child process with CPU, memory and wall-clock limits (a small
    file can ask for hours of work). Any failure to read is a 422, never a 500, and never stalls the API."""
    # at most a few readers at once: each holds a request thread for up to the sandbox's wall-clock limit
    if not _READERS.acquire(timeout=5):
        raise UploadRefused(503, "busy", "several files are being read right now; try again in a minute")
    try:
        return _read(kind, name, data)
    finally:
        _READERS.release()


def read_pdf(name: str, data: bytes, vision: Any) -> IngestResult:
    """A PDF: pages rendered in the sandbox (one slot), then read by the vision model, if one is configured. The result is evidence only."""
    if not _READERS.acquire(timeout=5):
        raise UploadRefused(503, "busy", "several files are being read right now; try again in a minute")
    try:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "upload.pdf"
            path.write_bytes(data)
            try:
                rendered = render_isolated(path)
            except IngestRefused as exc:
                raise UploadRefused(422, "too_complex" if "was stopped" in str(exc) else "unreadable_file", str(exc)) from None
    finally:
        _READERS.release()
    if not rendered.get("pages"):
        raise UploadRefused(422, "unreadable_file", "no page of this PDF could be drawn: " + ("; ".join(rendered.get("problems", [])[:2]) or "it has no pages"))
    return extract_rendered(name, hashlib.sha256(data).hexdigest(), rendered, vision)


def _read(kind: str, name: str, data: bytes) -> IngestResult:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"{Path(name).stem or 'upload'}.{kind}"
        path.write_bytes(data)
        try:
            return read_isolated(kind, path)
        except IngestRefused as exc:
            code = "too_complex" if "too complex" in str(exc) or "was stopped" in str(exc) else "unreadable_file"
            raise UploadRefused(422, code, str(exc)) from None


class PgUploads:
    def __init__(self, dsn: str, supabase_url: str, anon_key: str, vision: Any = None) -> None:
        self._dsn, self._url, self._anon, self._vision = dsn, supabase_url.rstrip("/"), anon_key, vision

    def _store(self, token: str, path: str, data: bytes) -> None:
        try:
            r = httpx.post(f"{self._url}/storage/v1/object/{BUCKET}/{path}", content=data, timeout=120,
                           headers={"apikey": self._anon, "Authorization": f"Bearer {token}",
                                    "Content-Type": "application/octet-stream", "x-upsert": "false"})
        except httpx.HTTPError:
            raise UploadRefused(502, "storage_unavailable", "the file store could not be reached") from None
        if r.status_code in (200, 201):
            return
        if r.status_code == 409 or "Duplicate" in r.text:       # something is stored at this path: prove it is THESE bytes
            self._verify(token, path, data)
            return
        if r.status_code in (400, 401, 403):
            raise UploadRefused(403, "storage_refused", "the file store refused this upload for your account")
        raise UploadRefused(502, "storage_failed", "the file could not be stored")

    def _verify(self, token: str, path: str, data: bytes) -> None:
        try:
            got = httpx.get(f"{self._url}/storage/v1/object/{BUCKET}/{path}", timeout=120,
                            headers={"apikey": self._anon, "Authorization": f"Bearer {token}"})
        except httpx.HTTPError:
            raise UploadRefused(502, "storage_unavailable", "the file store could not be reached") from None
        if got.status_code != 200 or hashlib.sha256(got.content).digest() != hashlib.sha256(data).digest():
            raise UploadRefused(409, "storage_conflict", "a different file is already stored under this file's name")

    @staticmethod
    def _undo_child(svc: psycopg.Connection[Any], user: CurrentUser, child: UUID | None) -> None:
        """Best effort: a child whose file could not be stored or ingested must not linger empty."""
        if child is None:
            return
        with contextlib.suppress(Exception):
            drop_empty_child(svc, user.firm_id, child)

    def _queue_pdf(self, user: CurrentUser, token: str, revision_id: UUID, name: str, sha: str, data: bytes) -> dict[str, Any]:
        """A PDF is read in the background: store the file as the user, enqueue the job, answer at once. The job's status is polled by the UI."""
        from mep.api.vision_jobs import enqueue

        path = f"{user.firm_id}/{revision_id}/{sha}.pdf"
        self._store(token, path, data)
        with psycopg.connect(self._dsn, autocommit=True) as svc:
            with svc.cursor() as cur:
                cur.execute("delete from vision_job where revision_id = %s and sha256 = %s and status = 'failed'", (revision_id, sha))
            try:
                job = enqueue(svc, user=user, revision_id=revision_id, name=name, sha=sha, storage_path=f"{BUCKET}/{path}", data=data)
            except psycopg.errors.UniqueViolation:
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision") from None
        return {"kind": "pdf", "name": name, "sha256": sha, "bytes": len(data), "storage_path": f"{BUCKET}/{path}", "revision_id": str(revision_id),
                "job_id": str(job), "status": "queued", "ingest_run": None, "spaces": 0, "extractions": 0, "health": None, "problems": []}

    def ingest(self, *, user: CurrentUser, token: str, revision_id: UUID, name: str, kind: str, data: bytes,
               architect_rev: str | None = None) -> dict[str, Any]:
        """Ingest into an open revision; for a FROZEN revision make a child revision (the architect re-issued the model) and
        ingest into that."""
        sha = hashlib.sha256(data).hexdigest()
        with psycopg.connect(self._dsn, autocommit=False) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (user.claims_json(),))
            rev = conn.execute("select frozen_at is not null, architect_rev, project_id from revision"
                               " where id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone()
            if rev is None:
                raise UploadRefused(404, "not_found", "revision not found")
            frozen, parent_label, project_id = bool(rev[0]), str(rev[1]), rev[2]
            if conn.execute("select 1 from ingest_run where revision_id = %s and source_sha256 = %s",
                            (revision_id, sha)).fetchone() or conn.execute(
                    "select 1 from vision_job where revision_id = %s and sha256 = %s and status <> 'failed'", (revision_id, sha)).fetchone():
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision")
        label = architect_rev or next_label(parent_label)
        if architect_rev is not None and not LABEL_OK.match(architect_rev):
            raise UploadRefused(422, "bad_label", "the architect revision label is 1-20 letters, digits, spaces, dots, dashes")
        if frozen and kind == "pdf":
            raise UploadRefused(409, "pdf_on_frozen", "a PDF is evidence only and does not make a new revision: add it to an open revision")
        if kind == "pdf":
            return self._queue_pdf(user, token, revision_id, name, sha, data)
        result = read_file(kind, name, data)
        target, child = revision_id, None
        with psycopg.connect(self._dsn, autocommit=True) as svc:
            if frozen:
                try:
                    child = target = create_child(svc, user.firm_id, revision_id, label, sha)
                except DuplicateChild:
                    raise UploadRefused(409, "already_uploaded", "this file already produced a new revision of this one") from None
            path = f"{user.firm_id}/{target}/{sha}.{kind}"
            try:
                self._store(token, path, data)
            except UploadRefused:
                self._undo_child(svc, user, child)
                raise
            result.source_name = name
            result.metadata["storage_path"] = f"{BUCKET}/{path}"
            try:
                stored = store_ingest(svc, firm_id=str(user.firm_id), revision_id=str(target), result=result)
            except AlreadyIngested:
                self._undo_child(svc, user, child)
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision") from None
            except psycopg.errors.RaiseException as exc:
                self._undo_child(svc, user, child)
                if "frozen" in str(exc):
                    raise UploadRefused(409, "revision_frozen", "revision is frozen") from None
                raise
            except Exception:
                self._undo_child(svc, user, child)
                raise
            carried: list[dict[str, Any]] = []
            if child is not None:
                carried = carry_confirmations(svc, user.firm_id, revision_id, child)
                ledger_created(svc, user.firm_id, child, revision_id, user.user_id, label, sha, carried)
        health = stored.get("health")
        out: dict[str, Any] = {
            "kind": kind, "name": name, "sha256": sha, "bytes": len(data), "storage_path": f"{BUCKET}/{path}",
            "revision_id": str(target), "ingest_run": stored["ingest_run"], "spaces": stored["spaces"],
            "extractions": stored["extractions"], "health": None if health is None else health_view(health),
            "problems": list(result.problems)}
        if child is not None:
            out["new_revision"] = {"id": str(child), "project_id": str(project_id), "architect_rev": label,
                                   "parent_revision_id": str(revision_id), "spaces_carried_unchanged": len(carried)}
        return out
