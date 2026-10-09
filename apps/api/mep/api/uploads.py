"""Upload endpoint for IFC and DXF files.

The file TYPE is decided from the file's CONTENT (never its name or the content type the client claims), the size is capped,
and only a designer of the revision's firm may upload. The service behind it (injected; the default refuses) stores the file
in Supabase Storage under the firm and runs the ingest and health score. Nothing uploaded is trusted: every value read from
the file lands as provenance 'extracted' and cannot reach the engine until a designer confirms it at Gate 1.
"""
import json
import re
from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from mep.api.schedule import CurrentUser

MAX_UPLOAD_BYTES = 50 * 1024 * 1024        # the storage bucket's own limit (migration 0007) is the same
UPLOAD_ROLES = frozenset({"designer"})

BODY_MARGIN = 1024 * 1024                  # multipart framing around the file
router = APIRouter()
# any POST whose path ends in /uploads (or /uploads/): FastAPI's UUID parameter also accepts the 32-hex, braced and urn:uuid
# spellings, and the app may be mounted under a prefix, so the guard must not depend on the canonical form
_UPLOAD_PATH = re.compile(r"/uploads/?$")


class UploadGuard:
    """ASGI middleware for the upload route: FastAPI reads a whole multipart body before it looks at the token, so refuse
    here, first, anything unauthenticated and anything too large (by Content-Length, and by counting chunked bodies)."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or not _UPLOAD_PATH.search(scope["path"]):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        limit = MAX_UPLOAD_BYTES + BODY_MARGIN
        if not headers.get("authorization", "").lower().startswith("bearer "):
            await self._reply(send, 401, "unauthenticated", "a bearer token is required")
            return
        declared = headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            await self._reply(send, 413, "too_large", "the upload is too large")
            return
        seen = 0
        exceeded = replied = False

        async def counting_receive() -> Any:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:                       # stop feeding the parser; whatever it answers is replaced below
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Any) -> None:
            nonlocal replied
            if not exceeded:
                await send(message)
            elif not replied:
                replied = True
                await self._reply(send, 413, "too_large", "the upload is too large")

        await self.app(scope, counting_receive, guarded_send)

    @staticmethod
    async def _reply(send: Any, status: int, code: str, message: str) -> None:
        body = json.dumps({"detail": {"code": code, "message": message}}).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class UploadRefused(Exception):
    """The service refuses the upload: carries the HTTP status and a stable code."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class UploadService(Protocol):
    def ingest(self, *, user: CurrentUser, token: str, revision_id: UUID, name: str, kind: str, data: bytes,
               architect_rev: str | None = None) -> dict[str, Any]:
        """Store the file under the firm, ingest it, return the summary. A FROZEN revision gets a child revision instead (the
        architect re-issued the model); `architect_rev` labels it. Raises UploadRefused."""


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
           authorization: Annotated[str | None, Header()] = None,
           architect_rev: Annotated[str | None, Form()] = None) -> dict[str, Any]:
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
                              data=data, architect_rev=(architect_rev or "").strip() or None)
    except UploadRefused as exc:
        raise _err(exc.status, exc.code, exc.message) from None
