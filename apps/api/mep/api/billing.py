"""Billing with Stripe, TEST MODE ONLY.

* The secret key must start with `sk_test_`; a live key is refused (this build must not take real money).
* A firm's subscription status gates the creation of NEW projects (see migration 0034); signed work is never locked.
* The webhook verifies Stripe's signature (HMAC-SHA256 over `timestamp.payload`, five-minute tolerance, constant-time compare), processes each event id once,
  and trusts nothing in the payload except what the signed event says. It never sees card data.
"""
import hashlib
import hmac
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from mep.api.schedule import CurrentUser

router = APIRouter()
PLANS_FILE = Path(__file__).resolve().parents[2] / "billing" / "plans.json"
STRIPE_API = "https://api.stripe.com/v1"
TOLERANCE_SECONDS = 300
STATUS_MAP = {"trialing": "trialing", "active": "active", "past_due": "past_due", "unpaid": "past_due", "incomplete": "past_due",
              "incomplete_expired": "canceled", "canceled": "canceled", "paused": "past_due"}


def current_user() -> CurrentUser:
    raise HTTPException(status_code=401, detail="authentication is not configured")


def get_dsn() -> str:
    raise HTTPException(status_code=503, detail="billing is not configured")


def get_stripe() -> Any:
    return None


User = Annotated[CurrentUser, Depends(current_user)]
Dsn = Annotated[str, Depends(get_dsn)]


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@contextmanager
def _as_user(dsn: str, user: CurrentUser) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(dsn, autocommit=False, row_factory=dict_row) as conn:
        conn.execute("set local role authenticated")
        conn.execute("select set_config('request.jwt.claims', %s, true)", (user.claims_json(),))
        yield conn


def load_plans() -> dict[str, Any]:
    return json.loads(PLANS_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------------------------------------------- Stripe client (test mode)
class StripeClient:
    def __init__(self, secret_key: str, webhook_secret: str, app_url: str, client: httpx.Client | None = None) -> None:
        if not secret_key.startswith("sk_test_"):
            raise ValueError("only a Stripe TEST key (sk_test_...) is accepted: this build must not take real payments")
        self._key, self.webhook_secret, self.app_url = secret_key, webhook_secret, app_url.rstrip("/")
        self._client = client or httpx.Client(timeout=20)

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        try:
            r = self._client.post(f"{STRIPE_API}{path}", data=data, auth=(self._key, ""))
        except httpx.HTTPError:
            raise _err(502, "stripe_unreachable", "the payment provider could not be reached") from None
        if r.status_code != 200:
            raise _err(502, "stripe_refused", "the payment provider refused the request")
        return r.json()  # type: ignore[no-any-return]

    def checkout(self, firm_id: str, customer: str | None, items: list[tuple[str, int]], trial_days: int | None) -> str:
        data: dict[str, Any] = {"mode": "subscription", "client_reference_id": firm_id, "subscription_data[metadata][firm_id]": firm_id,
                                "success_url": f"{self.app_url}/billing?checkout=done", "cancel_url": f"{self.app_url}/billing?checkout=cancelled"}
        if customer:
            data["customer"] = customer
        if trial_days:
            data["subscription_data[trial_period_days]"] = trial_days
        for i, (price, qty) in enumerate(items):
            data[f"line_items[{i}][price]"] = price
            data[f"line_items[{i}][quantity]"] = qty
        return str(self._post("/checkout/sessions", data)["url"])

    def portal(self, customer: str) -> str:
        return str(self._post("/billing_portal/sessions", {"customer": customer, "return_url": f"{self.app_url}/billing"})["url"])


def stripe_from_env() -> StripeClient | None:
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        return None
    return StripeClient(key, os.environ.get("STRIPE_WEBHOOK_SECRET", ""), os.environ.get("MEP_APP_URL", "http://localhost:3000"))


# ---------------------------------------------------------------------------------------------------------------- the signed webhook
def verify_signature(payload: bytes, header: str, secret: str, now: float | None = None) -> bool:
    """Stripe-Signature: `t=<unix>,v1=<hex>[,v1=<hex>...]`; v1 = HMAC-SHA256(secret, f"{t}.{payload}")."""
    if not secret or not header:
        return False
    parts = [p.split("=", 1) for p in header.split(",") if "=" in p]
    stamps = [v for k, v in parts if k == "t"]
    sigs = [v for k, v in parts if k == "v1"]
    if len(stamps) != 1 or not sigs or not stamps[0].isdigit():
        return False
    if abs((now if now is not None else time.time()) - int(stamps[0])) > TOLERANCE_SECONDS:
        return False
    expected = hmac.new(secret.encode(), f"{stamps[0]}.".encode() + payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, s) for s in sigs)


def _ts(value: Any) -> datetime | None:
    return None if not isinstance(value, int) else datetime.fromtimestamp(value, UTC)


def apply_event(conn: psycopg.Connection[Any], event: dict[str, Any]) -> str:
    """Update the firm's subscription from a verified event. Returns what was done."""
    kind, obj = str(event.get("type")), (event.get("data") or {}).get("object") or {}
    firm = (obj.get("metadata") or {}).get("firm_id") or obj.get("client_reference_id")
    if kind == "checkout.session.completed":
        if firm and obj.get("customer") and obj.get("subscription"):
            conn.execute("update subscription set stripe_customer_id = %s, stripe_subscription_id = %s, updated_at = now() where firm_id = %s",
                         (obj["customer"], obj["subscription"], firm))
            return "linked"
        return "ignored"
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        status = "canceled" if kind.endswith("deleted") else STATUS_MAP.get(str(obj.get("status")), "past_due")
        seats = next((int(i.get("quantity") or 0) for i in (obj.get("items") or {}).get("data", []) if i.get("quantity")), None)
        row = conn.execute(
            "update subscription set status = %s, plan_id = coalesce(%s, plan_id), seats = coalesce(%s, seats), max_projects = case when %s then %s else max_projects end,"
            " current_period_end = %s, stripe_customer_id = coalesce(stripe_customer_id, %s), stripe_subscription_id = coalesce(stripe_subscription_id, %s),"
            " updated_at = now() where stripe_subscription_id = %s or (%s::uuid is not null and firm_id = %s::uuid) returning firm_id",
            (status, (obj.get("metadata") or {}).get("plan_id"), seats, "plan_id" in (obj.get("metadata") or {}), _plan_max((obj.get("metadata") or {}).get("plan_id")),
             _ts(obj.get("current_period_end")), obj.get("customer"), obj.get("id"), obj.get("id"), firm, firm)).fetchone()
        return "updated" if row else "ignored"
    if kind in ("invoice.payment_failed", "invoice.paid"):
        sub = obj.get("subscription")
        if sub:
            row = conn.execute("update subscription set status = %s, updated_at = now() where stripe_subscription_id = %s returning firm_id",
                               ("past_due" if kind.endswith("failed") else "active", sub)).fetchone()
            return "updated" if row else "ignored"
    return "ignored"


def _plan_max(plan_id: Any) -> int | None:
    for p in load_plans()["plans"]:
        if p["id"] == plan_id:
            return p["max_projects"]  # type: ignore[no-any-return]
    return None


@router.post("/billing/webhook")
async def webhook(request: Request, dsn: Dsn, stripe: Annotated[Any, Depends(get_stripe)]) -> dict[str, Any]:
    if stripe is None or not stripe.webhook_secret:
        raise _err(503, "billing_off", "billing is not configured")
    payload = await request.body()
    if len(payload) > 1_000_000 or not verify_signature(payload, request.headers.get("stripe-signature", ""), stripe.webhook_secret):
        raise _err(400, "bad_signature", "the signature does not verify")
    try:
        event = json.loads(payload)
        eid = str(event["id"])
    except (ValueError, KeyError, TypeError):
        raise _err(400, "bad_event", "unreadable event") from None
    with psycopg.connect(dsn, autocommit=False) as conn:
        try:
            conn.execute("insert into stripe_event (id, type) values (%s, %s)", (eid, str(event.get("type"))[:80]))
        except psycopg.errors.UniqueViolation:
            return {"received": True, "duplicate": True}
        except psycopg.errors.CheckViolation:
            raise _err(400, "bad_event", "unreadable event id") from None
        done = apply_event(conn, event)
        conn.commit()
    return {"received": True, "result": done}


# ---------------------------------------------------------------------------------------------------------------- the firm's view and actions
@router.get("/billing")
def overview(user: User, dsn: Dsn, stripe: Annotated[Any, Depends(get_stripe)]) -> dict[str, Any]:
    with _as_user(dsn, user) as conn:
        sub = conn.execute("select status, plan_id, seats, max_projects, trial_ends_at, current_period_end, stripe_customer_id is not null as has_customer"
                           " from subscription where firm_id = %s", (user.firm_id,)).fetchone()
        projects = conn.execute("select count(*) as n from project where firm_id = %s", (user.firm_id,)).fetchone()["n"]
        seats = conn.execute("select count(*) as n from app_user where firm_id = %s and active", (user.firm_id,)).fetchone()["n"]
    plans = load_plans()
    return {"test_mode": True, "available": stripe is not None, "currency": plans["currency"], "plans": [
        {k: p[k] for k in ("id", "name", "seat_cents", "project_cents", "max_projects")} for p in plans["plans"]],
        "subscription": None if sub is None else {**sub, "trial_ends_at": sub["trial_ends_at"].isoformat(),
                                                   "current_period_end": None if sub["current_period_end"] is None else sub["current_period_end"].isoformat()},
        "projects": projects, "active_seats": seats}


class CheckoutBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,40}$")


def _admin(dsn: str, user: CurrentUser) -> None:
    with _as_user(dsn, user) as conn:
        if not conn.execute("select current_user_is_admin() as a").fetchone()["a"]:
            raise _err(403, "forbidden", "only an administrator of the firm manages billing")


@router.post("/billing/checkout")
def checkout(body: CheckoutBody, user: User, dsn: Dsn, stripe: Annotated[Any, Depends(get_stripe)]) -> dict[str, Any]:
    _admin(dsn, user)
    if stripe is None:
        raise _err(503, "billing_off", "billing is not configured on this server")
    plan = next((p for p in load_plans()["plans"] if p["id"] == body.plan_id), None)
    if plan is None:
        raise _err(422, "unknown_plan", "no such plan")
    seat_price = os.environ.get(plan["price_env_seat"] or "")
    if not seat_price:
        raise _err(503, "price_missing", "the Stripe test price for this plan is not configured")
    with _as_user(dsn, user) as conn:
        seats = max(1, conn.execute("select count(*) as n from app_user where firm_id = %s and active", (user.firm_id,)).fetchone()["n"])
        projects = max(1, conn.execute("select count(*) as n from project where firm_id = %s", (user.firm_id,)).fetchone()["n"])
        customer = (conn.execute("select stripe_customer_id as c from subscription where firm_id = %s", (user.firm_id,)).fetchone() or {}).get("c")
    items = [(seat_price, seats)]
    if plan["price_env_project"] and os.environ.get(plan["price_env_project"]):
        items.append((os.environ[plan["price_env_project"]], projects))
    url = stripe.checkout(str(user.firm_id), customer, items, None)
    return {"url": url}


@router.post("/billing/portal")
def portal(user: User, dsn: Dsn, stripe: Annotated[Any, Depends(get_stripe)]) -> dict[str, Any]:
    _admin(dsn, user)
    if stripe is None:
        raise _err(503, "billing_off", "billing is not configured on this server")
    with _as_user(dsn, user) as conn:
        row = conn.execute("select stripe_customer_id as c from subscription where firm_id = %s", (user.firm_id,)).fetchone()
    if not row or not row["c"]:
        raise _err(409, "no_customer", "subscribe first")
    return {"url": stripe.portal(row["c"])}


# ---------------------------------------------------------------------------------------------------------------- starting a project (gated)
class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str = Field(min_length=3, max_length=200)
    state: Literal["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"]
    ncc_edition: Literal["NCC2022", "NCC2025"]
    climate_zone: int | None = Field(default=None, ge=1, le=8)
    approval_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/projects")
def create_project(body: ProjectBody, user: User, dsn: Dsn) -> dict[str, Any]:
    try:
        with _as_user(dsn, user) as conn:
            row = conn.execute("select project_create(%s, %s, %s, %s, %s::date) as r",
                               (body.address, body.state, body.ncc_edition, body.climate_zone, body.approval_date)).fetchone()
    except psycopg.errors.InsufficientPrivilege as exc:
        raise _err(403, "forbidden", str(exc).splitlines()[0]) from None
    except psycopg.errors.DataError as exc:
        raise _err(422, "invalid", str(exc).splitlines()[0]) from None
    except psycopg.Error as exc:
        if getattr(exc, "sqlstate", None) == "P0402":
            raise _err(402, "payment_required", str(exc).splitlines()[0]) from None
        raise _err(422, "refused", str(exc).splitlines()[0]) from None
    except psycopg.errors.CheckViolation as exc:
        raise _err(422, "invalid", str(exc).splitlines()[0]) from None
    return dict(row["r"])      # type: ignore[index]
