"""Observability: Sentry error reporting with personal data removed, structured JSON logs with a request id, and the readiness check.

Nothing here logs or reports a request body, a query string, a header, a cookie, an e-mail address or a token. Sentry is optional (`pip install
'mep-copilot[observability]'`, SENTRY_DSN set); without either everything else still works.
"""
import json
import logging
import os
import re
import sys
import time
import uuid
from typing import Any

SENSITIVE_KEYS = re.compile(r"(authorization|cookie|token|secret|password|passwd|api[-_]?key|apikey|dsn|email|jwt|bearer|set-cookie|x-api-key|registration)", re.I)
EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LONG_SECRET = re.compile(r"\b(?:eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*|[A-Fa-f0-9]{40,}|sk_[A-Za-z0-9_]{10,}|re_[A-Za-z0-9_]{10,}|whsec_[A-Za-z0-9_]{10,})\b")
REDACTED = "[redacted]"


def scrub_text(text: str) -> str:
    return LONG_SECRET.sub(REDACTED, EMAIL.sub(REDACTED, text))


def scrub(value: Any, depth: int = 0) -> Any:
    """Return a copy with sensitive keys blanked and e-mail addresses / token-shaped strings removed from every string."""
    if depth > 8:
        return REDACTED
    if isinstance(value, dict):
        return {k: (REDACTED if isinstance(k, str) and SENSITIVE_KEYS.search(k) else scrub(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v, depth + 1) for v in value]
    if isinstance(value, str):
        return scrub_text(value)
    return value


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any] | None:
    """Sentry `before_send`: drop request bodies, headers, cookies and query strings, the user's address and IP, and scrub what is left."""
    req = event.get("request")
    if isinstance(req, dict):
        for k in ("data", "headers", "cookies", "query_string", "env"):
            req.pop(k, None)
        if isinstance(req.get("url"), str):
            req["url"] = req["url"].split("?", 1)[0].split("#", 1)[0]
    user = event.get("user")
    if isinstance(user, dict):
        event["user"] = {"id": user["id"]} if user.get("id") else {}
    event.pop("server_name", None)
    for key in ("extra", "contexts", "tags", "breadcrumbs", "exception", "message", "logentry", "transaction"):
        if key in event:
            event[key] = scrub(event[key])
    return event


def init_sentry(service: str) -> bool:
    """Start Sentry for this process if SENTRY_DSN is set and the SDK is installed. Returns whether it started."""
    dsn = os.environ.get("SENTRY_DSN", "")
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        return False
    sentry_sdk.init(dsn=dsn, send_default_pii=False, attach_stacktrace=True, max_request_body_size="never", before_send=scrub_event,
                    before_breadcrumb=lambda crumb, hint: scrub(crumb), traces_sample_rate=float(os.environ.get("SENTRY_TRACES_RATE", "0")),
                    release=os.environ.get("MEP_RELEASE") or None, environment=os.environ.get("MEP_ENV", "development"),
                    server_name="")
    sentry_sdk.set_tag("service", service)
    return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
                               "level": record.levelname, "logger": record.name, "msg": scrub_text(record.getMessage())}
        for k in ("request_id", "method", "path", "status", "ms", "service"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info and record.exc_info[0] is not None:
            out["error"] = record.exc_info[0].__name__          # the type only: the message can hold row contents
        return json.dumps(out, separators=(",", ":"))


def configure_logging(service: str) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(lambda r: setattr(r, "service", service) or True)
    root.handlers[:] = [handler]
    root.setLevel(os.environ.get("MEP_LOG_LEVEL", "INFO"))


class RequestLogMiddleware:
    """ASGI middleware: a request id on every response, and one structured log line per request (method, path without query, status, milliseconds)."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.log = logging.getLogger("mep.request")

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        rid = next((v.decode() for k, v in scope["headers"] if k == b"x-request-id" and re.fullmatch(rb"[A-Za-z0-9\-]{8,64}", v)), None) or uuid.uuid4().hex
        start, status = time.monotonic(), {"code": 500}

        async def send_wrapper(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if path not in ("/healthz", "/readyz"):
                self.log.info("request", extra={"request_id": rid, "method": scope.get("method"), "path": path[:200], "status": status["code"],
                                                "ms": int((time.monotonic() - start) * 1000)})
