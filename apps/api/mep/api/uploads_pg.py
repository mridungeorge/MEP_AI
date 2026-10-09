"""Postgres and Supabase Storage behind the upload endpoint.

Order of work (each step can only fail closed): look the revision up as the user (RLS, firm scope, frozen check) -> refuse a
file already ingested into this revision -> PARSE the file (an unreadable file leaves nothing behind) -> store it in the private
`uploads` bucket as the USER (storage RLS: own firm, own revision, designer only) -> write ingest_run, extraction evidence and
the space rows on a service connection. Spaces are always provenance 'extracted'.
"""
import hashlib
import json
import tempfile
import threading
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg

from mep.api.lineage_pg import (
    LABEL_OK,
    carry_confirmations,
    create_child,
    drop_empty_child,
    ledger_created,
    next_label,
)
from mep.api.pg import health_view
from mep.api.schedule import CurrentUser
from mep.api.uploads import UploadRefused
from mep.ingest.records import IngestRefused, IngestResult
from mep.ingest.sandbox import read_isolated
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
    def __init__(self, dsn: str, supabase_url: str, anon_key: str) -> None:
        self._dsn, self._url, self._anon = dsn, supabase_url.rstrip("/"), anon_key

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

    def ingest(self, *, user: CurrentUser, token: str, revision_id: UUID, name: str, kind: str, data: bytes,
               architect_rev: str | None = None) -> dict[str, Any]:
        """Ingest into an open revision; for a FROZEN revision make a child revision (the architect re-issued the model) and
        ingest into that."""
        sha = hashlib.sha256(data).hexdigest()
        with psycopg.connect(self._dsn, autocommit=False) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (json.dumps({"sub": str(user.user_id), "role": "authenticated"}),))
            rev = conn.execute("select frozen_at is not null, architect_rev, project_id from revision"
                               " where id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone()
            if rev is None:
                raise UploadRefused(404, "not_found", "revision not found")
            frozen, parent_label, project_id = bool(rev[0]), str(rev[1]), rev[2]
            if conn.execute("select 1 from ingest_run where revision_id = %s and source_sha256 = %s",
                            (revision_id, sha)).fetchone():
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision")
        label = architect_rev or next_label(parent_label)
        if architect_rev is not None and not LABEL_OK.match(architect_rev):
            raise UploadRefused(422, "bad_label", "the architect revision label is 1-20 letters, digits, spaces, dots, dashes")
        result = read_file(kind, name, data)
        target, child = revision_id, None
        with psycopg.connect(self._dsn, autocommit=True) as svc:
            if frozen:
                child = target = create_child(svc, user.firm_id, revision_id, label)
            path = f"{user.firm_id}/{target}/{sha}.{kind}"
            try:
                self._store(token, path, data)
            except UploadRefused:
                if child is not None:
                    drop_empty_child(svc, user.firm_id, child)
                raise
            result.source_name = name
            result.metadata["storage_path"] = f"{BUCKET}/{path}"
            try:
                stored = store_ingest(svc, firm_id=str(user.firm_id), revision_id=str(target), result=result)
            except AlreadyIngested:
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision") from None
            except psycopg.errors.RaiseException as exc:
                if "frozen" in str(exc):
                    raise UploadRefused(409, "revision_frozen", "revision is frozen") from None
                raise
            carried = 0
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
                                   "parent_revision_id": str(revision_id), "spaces_carried_unchanged": carried}
        return out
