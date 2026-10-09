"""Upload endpoint for IFC and DXF files.

The file TYPE is decided from the file's CONTENT (never its name or the content type the client claims), the size is capped,
and only a designer of the revision's firm may upload. The service behind it (injected; the default refuses) stores the file
in Supabase Storage under the firm and runs the ingest and health score. Nothing uploaded is trusted: every value read from
the file lands as provenance 'extracted' and cannot reach the engine until a designer confirms it at Gate 1.
"""
import re
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile

from mep.api.schedule import CurrentUser

MAX_UPLOAD_BYTES = 50 * 1024 * 1024        # the storage bucket's own limit (migration 0007) is the same
UPLOAD_ROLES = frozenset({"designer"})

router = APIRouter()


class UploadRefused(Exception):
    """The service refuses the upload: carries the HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class UploadService(Protocol):
    def ingest(self, *, user: CurrentUser, token: str, revision_id: UUID, name: str, kind: str, data: bytes) -> dict[str, Any]:
        """Store the file under the firm, ingest it, return the summary. Raises UploadRefused."""


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_service() -> UploadService:
    raise HTTPException(status_code=503, detail={"code": "uploads_unavailable", "message": "uploads are not configured"})


User = Annotated[CurrentUser, Depends(current_user)]
Service = Annotated[UploadService, Depends(get_service)]

_IFC_START = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*ISO-10303-21\s*;")
_IFC_SCHEMA = re.compile(rb"FILE_SCHEMA\s*\(\s*\(\s*'IFC", re.IGNORECASE)
_DXF_ASCII = re.compile(rb"\A(?:\xef\xbb\xbf)?\s*0\s*\r?\n\s*SECTION\s*\r?\n\s*2\s*\r?\n")
_DXF_BINARY = b"AutoCAD Binary DXF\r\n\x1a\x00"
_DWG = re.compile(rb"\AAC10\d\d")


def sniff(data: bytes) -> tuple[str | None, str]:
    """(kind, '') for an IFC or DXF file recognised by its content, else (None, why it was refused)."""
    head = data[:20_000]
    if _IFC_START.match(head) and _IFC_SCHEMA.search(head):
        return "ifc", ""
    if _DXF_ASCII.match(head) or head.startswith(_DXF_BINARY):
        return "dxf", ""
    if _DWG.match(head):
        return None, "DWG files are not accepted: export the drawing as DXF"
    if head.startswith(b"PK\x03\x04"):
        return None, "zipped files are not accepted: upload the .ifc or .dxf itself"
    if head.startswith(b"%PDF"):
        return None, "PDF files are not accepted here yet"
    return None, "not a recognised IFC (STEP) or DXF file"


def safe_name(name: str | None) -> str:
    """A display name for the file: no path, no odd characters, bounded."""
    base = re.split(r"[\\/]", name or "")[-1]
    return re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")[:100] or "upload"


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post("/revisions/{revision_id}/uploads")
def upload(revision_id: UUID, user: User, service: Service, file: Annotated[UploadFile, File()],
           authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    if user.role not in UPLOAD_ROLES:
        raise _err(403, "forbidden", "only a designer uploads files")
    data = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise _err(413, "too_large", f"files are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB")
    kind, why = sniff(data)
    if kind is None:
        raise _err(415, "unsupported_file", why)
    token = (authorization or "").partition(" ")[2].strip()
    try:
        return service.ingest(user=user, token=token, revision_id=revision_id, name=safe_name(file.filename), kind=kind,
                              data=data)
    except UploadRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
