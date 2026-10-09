"""Gate 2 review, Gate 2/3 sign-off, ledger check, the signed package (JSON and PDF) and certifier share links.

The API only carries requests to the security-definer functions (migration 0010) and returns their refusals verbatim; it never decides
a gate itself. The public /share routes need no sign-in: the long random token IS the credential, it opens one revision's package,
read-only, for a limited time, and each opening is logged in the ledger.
"""
import os
import time
from typing import Annotated, Any, Literal, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from mep.api.review_pg import ReviewRefused
from mep.api.schedule import CurrentUser
from mep.review import package as pkg

router = APIRouter()


class ReviewService(Protocol):
    def worksheet(self, revision_id: UUID) -> dict[str, Any]: ...
    def review(self, result_id: UUID, decision: str, reason: str, sample_id: UUID | None,
               fail_category: str | None = None, fail_reference: str | None = None) -> int: ...
    def acknowledge_fail(self, result_id: UUID, note: str) -> int: ...
    def prepare_bulk(self, revision_id: UUID) -> str: ...
    def bulk_approve(self, sample_id: UUID) -> int: ...
    def sign(self, revision_id: UUID, gate: str, registration: str | None) -> int: ...
    def verify_ledger(self) -> dict[str, Any]: ...
    def create_share(self, revision_id: UUID, days: int, label: str | None) -> dict[str, Any]: ...
    def revoke_share(self, revision_id: UUID, link_id: str) -> None: ...
    def share_links(self, revision_id: UUID) -> list[dict[str, Any]]: ...
    def package(self, revision_id: UUID) -> dict[str, Any]: ...
    def record_artifact(self, revision_id: UUID, data: bytes, validator: dict[str, Any], complete: bool) -> bool: ...


class ShareService(Protocol):
    def exchange(self, token: str, client: str | None) -> tuple[str, int] | None: ...
    def read(self, session: str, what: str) -> dict[str, Any] | None: ...


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_service(user: Annotated[CurrentUser, Depends(current_user)]) -> ReviewService:
    raise HTTPException(status_code=503, detail="review is not configured")


def get_share() -> ShareService:
    raise HTTPException(status_code=503, detail="sharing is not configured")


Service = Annotated[ReviewService, Depends(get_service)]
Share = Annotated[ShareService, Depends(get_share)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _run(fn: Any, *args: Any) -> Any:
    try:
        return fn(*args)
    except ReviewRefused as exc:
        raise _err(exc.status, "refused" if exc.status != 404 else "not_found", str(exc)) from None


class DecisionBody(BaseModel):
    result_id: UUID
    decision: Literal["approve", "reject", "request_changes"]
    reason: Annotated[str, Field(min_length=3, max_length=2000)]
    sample_id: UUID | None = None
    fail_category: Literal["performance_solution", "rule_disputed", "out_of_scope"] | None = None
    fail_reference: Annotated[str, Field(max_length=500)] | None = None


class AckBody(BaseModel):
    result_id: UUID
    note: Annotated[str, Field(min_length=3, max_length=2000)]


class SampleBody(BaseModel):
    sample_id: UUID


class SignBody(BaseModel):
    registration: Annotated[str, Field(max_length=40)] | None = None


class ShareBody(BaseModel):
    days: Annotated[int, Field(ge=1, le=30)] = 7
    label: Annotated[str, Field(max_length=80)] | None = None


@router.get("/revisions/{revision_id}/review")
def get_worksheet(revision_id: UUID, user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> dict[str, Any]:
    return _run(svc.worksheet, revision_id)  # type: ignore[no-any-return]


@router.post("/revisions/{revision_id}/review/decisions")
def post_decision(revision_id: UUID, body: DecisionBody, user: Annotated[CurrentUser, Depends(current_user)],
                  svc: Service) -> dict[str, Any]:
    return {"seq": _run(svc.review, body.result_id, body.decision, body.reason, body.sample_id, body.fail_category, body.fail_reference)}


@router.post("/revisions/{revision_id}/review/acknowledge-fail")
def acknowledge_fail(revision_id: UUID, body: AckBody, user: Annotated[CurrentUser, Depends(current_user)],
                     svc: Service) -> dict[str, Any]:
    """The approver acknowledges ONE accepted FAIL (there is no bulk form)."""
    return {"id": _run(svc.acknowledge_fail, body.result_id, body.note)}


@router.post("/revisions/{revision_id}/review/bulk/prepare")
def prepare_bulk(revision_id: UUID, user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> dict[str, Any]:
    """Draw the random spot-check sample of the unreviewed clean passes."""
    return {"sample_id": _run(svc.prepare_bulk, revision_id)}


@router.post("/revisions/{revision_id}/review/bulk/approve")
def bulk_approve(revision_id: UUID, body: SampleBody, user: Annotated[CurrentUser, Depends(current_user)],
                 svc: Service) -> dict[str, Any]:
    return {"approved": _run(svc.bulk_approve, body.sample_id)}


@router.post("/revisions/{revision_id}/sign/{gate}")
def sign(revision_id: UUID, gate: Literal["gate2", "gate3"], body: SignBody, user: Annotated[CurrentUser, Depends(current_user)],
         svc: Service) -> dict[str, Any]:
    return {"signed": gate, "ledger_anchor": _run(svc.sign, revision_id, gate, body.registration)}


@router.get("/ledger/verify")
def verify(user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> dict[str, Any]:
    return _run(svc.verify_ledger)  # type: ignore[no-any-return]


@router.get("/revisions/{revision_id}/package")
def get_package(revision_id: UUID, user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> dict[str, Any]:
    return _run(svc.package, revision_id)  # type: ignore[no-any-return]


def _validated_pdf(package: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
    """A PDF whose validator failed is never returned (non-negotiable 9)."""
    data = pkg.to_pdf(package)
    validator = pkg.validate_pdf(data, package)
    if not validator["passed"]:
        failed = sorted(k for k, ok in validator["checks"].items() if not ok)
        raise _err(500, "validator_failed", "the report failed its own checks and is withheld: " + ", ".join(failed))
    return data, validator


def _pdf(svc: ReviewService, revision_id: UUID, package: dict[str, Any]) -> Response:
    data, validator = _validated_pdf(package)
    svc.record_artifact(revision_id, data, validator, package["status"]["complete"] and package["ledger"]["verified"])
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="compliance-{revision_id}.pdf"', "X-Validator": "passed"})


@router.get("/revisions/{revision_id}/package.pdf")
def get_package_pdf(revision_id: UUID, user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> Response:
    return _pdf(svc, revision_id, _run(svc.package, revision_id))


@router.post("/revisions/{revision_id}/share-links")
def create_share(revision_id: UUID, body: ShareBody, user: Annotated[CurrentUser, Depends(current_user)],
                 svc: Service) -> dict[str, Any]:
    """The token is returned ONCE; only its hash is stored."""
    return _run(svc.create_share, revision_id, body.days, body.label)  # type: ignore[no-any-return]


@router.get("/revisions/{revision_id}/share-links")
def list_share(revision_id: UUID, user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> list[dict[str, Any]]:
    return svc.share_links(revision_id)


@router.delete("/revisions/{revision_id}/share-links/{link_id}")
def revoke_share(revision_id: UUID, link_id: Annotated[str, Path(pattern=r"^[0-9a-f]{12}$")],
                 user: Annotated[CurrentUser, Depends(current_user)], svc: Service) -> dict[str, Any]:
    _run(svc.revoke_share, revision_id, link_id)
    return {"revoked": True}


# ---- the public, read-only door -------------------------------------------------------------------------------------
# The link token travels in the URL FRAGMENT (never sent to a server), the page POSTs it once to /share/exchange and gets a short-lived
# HttpOnly session cookie; nothing below has a token in its URL, so no access log, proxy log or Referer can carry one.

EXCHANGES_PER_MINUTE = 20
COOKIE = "mep_share"
_attempts: dict[str, list[float]] = {}


def _client_key(request: Request) -> str:
    """The throttle key: the RIGHTMOST X-Forwarded-For hop (the one our own proxy appended; the left ones are the client's to forge)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    return hops[-1] if hops else (request.client.host if request.client else "?")


def _throttle(client: str) -> None:
    now = time.monotonic()
    recent = [t for t in _attempts.get(client, []) if now - t < 60]
    if len(recent) >= EXCHANGES_PER_MINUTE:
        raise _err(429, "too_many_requests", "too many attempts; wait a minute")
    _attempts[client] = [*recent, now]
    if len(_attempts) > 10_000:       # drop the oldest half, never everybody's history at once
        for key in list(_attempts)[:5_000]:
            _attempts.pop(key, None)


class ExchangeBody(BaseModel):
    token: Annotated[str, Field(min_length=20, max_length=200)]


@router.post("/share/exchange")
def share_exchange(body: ExchangeBody, request: Request, share: Share) -> Response:
    import json
    _throttle(_client_key(request))
    got = share.exchange(body.token, request.headers.get("user-agent"))
    if got is None:      # unknown, expired and revoked look the same
        raise _err(404, "not_found", "this link is not valid or has expired")
    session, seconds = got
    secure = request.url.scheme == "https" or os.environ.get("MEP_COOKIE_SECURE") == "1"
    response = Response(content=json.dumps({"ok": True, "expires_in": seconds}), media_type="application/json",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    response.set_cookie(COOKIE, session, max_age=seconds, httponly=True, samesite="strict", secure=secure, path="/")
    return response


def _session(share: ShareService, request: Request, what: str) -> dict[str, Any]:
    package = share.read(request.cookies.get(COOKIE, ""), what)
    if package is None:
        raise _err(401, "no_session", "open the link again: this session has ended")
    return package


@router.get("/share/package")
def share_package(request: Request, share: Share) -> Response:
    import json
    return Response(content=json.dumps(_session(share, request, "package")), media_type="application/json",
                    headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@router.get("/share/report.pdf")
def share_pdf(request: Request, share: Share) -> Response:
    data, _ = _validated_pdf(_session(share, request, "pdf"))
    return Response(content=data, media_type="application/pdf", headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Content-Disposition": 'inline; filename="compliance-package.pdf"'})
