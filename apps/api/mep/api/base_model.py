"""The architect's IFC kept for a revision: uploaded once by a designer, listed with its storeys, downloadable by the firm (for the 3D preview) and handed to the
ifc-mep drafting job as its read-only `base.ifc`. Nothing here decides compliance; the file is stored as received (checked to be a readable IFC).
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response
from psycopg.rows import dict_row
from starlette.concurrency import run_in_threadpool

from mep.api.revisions import DESIGNER_ROLES, Repo, _err
from mep.api.revisions import User as RevUser
from mep.api.services import Dsn, _as_user

router = APIRouter()
MAX_BYTES = 100 * 1024 * 1024
MAX_MODELS = 5


def read_header(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """(schema, storeys) of an IFC file. Raises ValueError for anything that is not a readable IFC."""
    import ifcopenshell
    from ifcopenshell.util import unit

    try:
        f = ifcopenshell.open(str(path))
    except Exception as exc:
        raise ValueError("not a readable IFC file") from exc
    scale_mm = float(unit.calculate_unit_scale(f)) * 1000.0
    storeys = [{"name": (s.Name or "")[:80], "elevation_mm": round(float(s.Elevation or 0.0) * scale_mm, 1)} for s in f.by_type("IfcBuildingStorey")][:200]
    return str(f.schema), storeys


def read_header_isolated(path: Path) -> tuple[str, list[dict[str, Any]]]:
    """read_header in a child process with time, CPU and memory limits: a hostile file cannot take the API down. Raises ValueError for anything unreadable."""
    try:
        done = subprocess.run([sys.executable, "-I", "-m", "mep.api.base_model", str(path)], capture_output=True, timeout=130, check=False, cwd=str(path.parent))
    except subprocess.TimeoutExpired:
        raise ValueError("reading the model took too long") from None
    if done.returncode != 0:
        raise ValueError("not a readable IFC file")
    try:
        data = json.loads(done.stdout.strip().splitlines()[-1])
        return str(data["schema"]), list(data["storeys"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("not a readable IFC file") from None


@router.post("/revisions/{revision_id}/base-model")
async def upload(revision_id: UUID, user: RevUser, repo: Repo, dsn: Dsn, file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    info = repo.revision_info(revision_id, user.firm_id)
    if info is None:
        raise _err(404, "not_found", "revision not found")
    if user.role not in DESIGNER_ROLES:
        raise _err(403, "forbidden", "only a designer uploads the architect's model")
    if info["frozen"]:
        raise _err(409, "revision_frozen", "the revision is frozen")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise _err(413, "too_large", "a model is at most 100 MiB")
    if not data[:64].lstrip().startswith(b"ISO-10303-21"):
        raise _err(422, "not_ifc", "that is not an IFC (STEP) file")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.ifc"
        path.write_bytes(data)
        try:
            schema, storeys = await run_in_threadpool(read_header_isolated, path)
        except ValueError as exc:
            raise _err(422, "unreadable_ifc", str(exc)) from None
    if not schema.startswith("IFC4"):
        raise _err(422, "unsupported_schema", f"the model is {schema}; IFC4 or newer is needed to add services")
    digest = hashlib.sha256(data).hexdigest()
    name = ((file.filename or "model.ifc").replace("\\", "/").split("/")[-1])[:200]
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("select 1 from revision where id = %s and firm_id = %s for update", (revision_id, user.firm_id))      # serialises uploads of one revision
            n = conn.execute("select count(*) as n from base_model where revision_id = %s and firm_id = %s", (revision_id, user.firm_id)).fetchone()
            if n is not None and n["n"] >= MAX_MODELS:
                raise _err(409, "too_many_models", f"at most {MAX_MODELS} architect models per revision")
            row = conn.execute("insert into base_model (firm_id, revision_id, file_name, file_sha256, schema_name, storeys, content, created_by)"
                               " values (%s, %s, %s, %s, %s, %s::jsonb, %s, %s) returning id",
                               (user.firm_id, revision_id, name, digest, schema, json.dumps(storeys), data, user.user_id)).fetchone()
    except psycopg.errors.UniqueViolation:
        raise _err(409, "duplicate", "that exact file is already uploaded to this revision") from None
    except psycopg.errors.RaiseException as exc:
        raise _err(409, "revision_frozen", str(exc).splitlines()[0]) from None
    assert row is not None
    return {"id": str(row["id"]), "file_sha256": digest, "schema": schema, "storeys": storeys}


@router.get("/revisions/{revision_id}/base-models")
def listing(revision_id: UUID, user: RevUser, repo: Repo, dsn: Dsn) -> dict[str, Any]:
    if repo.revision_info(revision_id, user.firm_id) is None:
        raise _err(404, "not_found", "revision not found")
    with _as_user(dsn, user) as conn:
        rows = conn.execute("select id, file_name, file_sha256, schema_name, storeys, created_at from base_model where revision_id = %s and firm_id = %s order by created_at",
                            (revision_id, user.firm_id)).fetchall()
    return {"models": [{**r, "id": str(r["id"]), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/base-models/{model_id}/file")
def download(model_id: UUID, user: RevUser, dsn: Dsn) -> Response:
    with _as_user(dsn, user) as conn:
        row = conn.execute("select * from base_model_file(%s)", (model_id,)).fetchone()
    if row is None:
        raise _err(404, "not_found", "no such model")
    return Response(content=bytes(row["content"]), media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="model.ifc"', "X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


if __name__ == "__main__":  # pragma: no cover - the child process
    if os.name == "posix":
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
        resource.setrlimit(resource.RLIMIT_AS, (3 * 1024 * 1024 * 1024, 3 * 1024 * 1024 * 1024))
    try:
        _schema, _storeys = read_header(Path(sys.argv[1]))
    except ValueError:
        sys.exit(2)
    print(json.dumps({"schema": _schema, "storeys": _storeys}))
