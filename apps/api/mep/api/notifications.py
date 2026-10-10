"""E-mail notifications: the sender (Resend), the outbox runner, and each person's preferences.

The database writes the outbox (who is told what, honouring preferences). This module only delivers it. A message carries a subject, one sentence and a
link into the app: never a result, a value or a document. Without RESEND_API_KEY nothing is sent and the rows wait.
"""
import contextlib
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from uuid import UUID  # noqa: F401 - kept for route signatures in later phases

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from mep.api.schedule import CurrentUser

router = APIRouter()
RESEND_URL = "https://api.resend.com/emails"
MAX_ATTEMPTS = 3
KINDS = ("review_requested", "changes_requested", "signed", "share_link_opened")


class ResendMailer:
    """Sends through the Resend HTTP API. The key is used only in the Authorization header and never logged or put in an error."""

    def __init__(self, api_key: str, from_addr: str, app_url: str, client: httpx.Client | None = None) -> None:
        self._key, self.from_addr, self.app_url = api_key, from_addr, app_url.rstrip("/")
        self._client = client or httpx.Client(timeout=15)

    def send(self, to: str, subject: str, text: str) -> bool:
        try:
            r = self._client.post(RESEND_URL, headers={"Authorization": f"Bearer {self._key}"},
                                  json={"from": self.from_addr, "to": [to], "subject": subject[:200], "text": text})
        except httpx.HTTPError:
            return False
        return r.status_code in (200, 201, 202)

    def invitation(self, email: str, firm: str, role: str) -> bool:
        return self.send(email, f"{firm} has invited you to MEP Co-pilot",
                         f"{firm} has invited you to join as a {role}.\n\nSign in with this e-mail address at {self.app_url}/login ; "
                         "you will be asked to join the firm after you sign in.")


def mailer_from_env() -> ResendMailer | None:
    key = os.environ.get("RESEND_API_KEY", "")
    if not key:
        return None
    return ResendMailer(key, os.environ.get("MEP_MAIL_FROM", "MEP Co-pilot <notifications@example.invalid>"),
                        os.environ.get("MEP_APP_URL", "http://localhost:3000"))


def process_outbox(dsn: str, mailer: ResendMailer, limit: int = 50) -> int:
    """Deliver pending notifications. A failure is retried up to MAX_ATTEMPTS times, then marked failed."""
    sent = 0
    with psycopg.connect(dsn, autocommit=True, row_factory=dict_row) as conn:
        for _ in range(limit):
            with conn.transaction():
                row = conn.execute(
                    "select n.id, n.subject, n.body, n.link_path, n.attempts, u.email from notification n join app_user u on u.id = n.user_id"
                    " where n.status = 'pending' and n.attempts < %s order by n.created_at for update of n skip locked limit 1", (MAX_ATTEMPTS,)).fetchone()
                if row is None:
                    break
                text = row["body"] + (f"\n\nOpen: {mailer.app_url}{row['link_path']}" if row["link_path"] else "") + \
                    "\n\nYou can change which e-mails you get in the app under Notifications."
                ok = bool(row["email"]) and mailer.send(row["email"], row["subject"], text)
                attempts = row["attempts"] + 1
                conn.execute("update notification set attempts = %s, status = %s, sent_at = case when %s then now() end, error = %s where id = %s",
                             (attempts, "sent" if ok else ("failed" if attempts >= MAX_ATTEMPTS else "pending"), ok,
                              None if ok else "the e-mail provider did not accept it", row["id"]))
                sent += 1 if ok else 0
    return sent


class NotificationRunner:
    def __init__(self, dsn: str, mailer: ResendMailer, poll: float = 5.0) -> None:
        self._dsn, self._mailer, self._poll = dsn, mailer, poll
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="notification-runner", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)

    def _loop(self) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                process_outbox(self._dsn, self._mailer)
            self._stop.wait(self._poll)


# ---- preferences ---------------------------------------------------------------------------------------------------------------
def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="notifications are not configured")


User = Annotated[CurrentUser, Depends(current_user)]
Dsn = Annotated[str, Depends(get_dsn)]


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


@router.get("/me/notifications")
def prefs(user: User, dsn: Dsn) -> dict[str, Any]:
    with _as_user(dsn, user) as conn:
        stored = conn.execute("select my_notify_prefs() as p").fetchone()["p"] or {}
    return {"kinds": {k: bool(stored.get(k, True)) for k in KINDS}}


class PrefsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_requested: bool = True
    changes_requested: bool = True
    signed: bool = True
    share_link_opened: bool = True


@router.put("/me/notifications")
def set_prefs(body: PrefsBody, user: User, dsn: Dsn) -> dict[str, Any]:
    import json
    with _as_user(dsn, user) as conn:
        conn.execute("select set_my_notify_prefs(%s::jsonb)", (json.dumps(body.model_dump()),))
    return {"saved": True}
