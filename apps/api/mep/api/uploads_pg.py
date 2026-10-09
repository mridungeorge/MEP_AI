"""Postgres and Supabase Storage behind the upload endpoint.

Order of work (each step can only fail closed): look the revision up as the user (RLS, firm scope, frozen check) -> refuse a
file already ingested into this revision -> PARSE the file (an unreadable file leaves nothing behind) -> store it in the private
`uploads` bucket as the USER (storage RLS: own firm, own revision, designer only) -> write ingest_run, extraction evidence and
the space rows on a service connection. Spaces are always provenance 'extracted'.
"""
import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg

from mep.api.pg import health_view
from mep.api.schedule import CurrentUser
from mep.api.uploads import UploadRefused
from mep.ingest.records import IngestResult
from mep.ingest.store import AlreadyIngested, store_ingest

BUCKET = "uploads"


def read_file(kind: str, name: str, data: bytes) -> IngestResult:
    """Parse the uploaded bytes with the reader for `kind`. Any failure to read is a 422, never a 500."""
    from mep.ingest.dxf import read_dxf
    from mep.ingest.ifc import read_ifc

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / f"{Path(name).stem or 'upload'}.{kind}"
        path.write_bytes(data)
        try:
            return read_ifc(path) if kind == "ifc" else read_dxf(path)
        except Exception as exc:  # noqa: BLE001 - the readers raise many types for a damaged file
            raise UploadRefused(422, "unreadable_file", f"the file could not be read as {kind.upper()} ({type(exc).__name__})") from None


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
        if r.status_code == 409 or "Duplicate" in r.text:       # same bytes stored earlier (a retry): the path is the hash
            return
        if r.status_code in (400, 401, 403):
            raise UploadRefused(403, "storage_refused", "the file store refused this upload for your account")
        raise UploadRefused(502, "storage_failed", "the file could not be stored")

    def ingest(self, *, user: CurrentUser, token: str, revision_id: UUID, name: str, kind: str, data: bytes) -> dict[str, Any]:
        sha = hashlib.sha256(data).hexdigest()
        with psycopg.connect(self._dsn, autocommit=False) as conn:
            conn.execute("set local role authenticated")
            conn.execute("select set_config('request.jwt.claims', %s, true)",
                         (json.dumps({"sub": str(user.user_id), "role": "authenticated"}),))
            rev = conn.execute("select frozen_at is not null from revision where id = %s and firm_id = %s",
                               (revision_id, user.firm_id)).fetchone()
            if rev is None:
                raise UploadRefused(404, "not_found", "revision not found")
            if rev[0]:
                raise UploadRefused(409, "revision_frozen", "revision is frozen")
            if conn.execute("select 1 from ingest_run where revision_id = %s and source_sha256 = %s",
                            (revision_id, sha)).fetchone():
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision")
        result = read_file(kind, name, data)
        path = f"{user.firm_id}/{revision_id}/{sha}.{kind}"
        self._store(token, path, data)
        result.source_name = name
        result.metadata["storage_path"] = f"{BUCKET}/{path}"
        with psycopg.connect(self._dsn, autocommit=True) as svc:
            try:
                stored = store_ingest(svc, firm_id=str(user.firm_id), revision_id=str(revision_id), result=result)
            except AlreadyIngested:
                raise UploadRefused(409, "already_uploaded", "this file was already uploaded to this revision") from None
            except psycopg.errors.RaiseException as exc:
                if "frozen" in str(exc):
                    raise UploadRefused(409, "revision_frozen", "revision is frozen") from None
                raise
        health = stored.get("health")
        return {"kind": kind, "name": name, "sha256": sha, "bytes": len(data), "storage_path": f"{BUCKET}/{path}",
                "ingest_run": stored["ingest_run"], "spaces": stored["spaces"], "extractions": stored["extractions"],
                "health": None if health is None else health_view(health), "problems": list(result.problems)}
