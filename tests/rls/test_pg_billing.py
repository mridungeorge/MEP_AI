"""Billing: the signed Stripe webhook, checkout (test mode only) and the gate on starting new projects. Needs the local Supabase."""
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient
from mep.api.auth import mint_token
from mep.api.billing import StripeClient, verify_signature
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL, uid

SECRET = "whsec_testsecret"
captured: list[httpx.Request] = []


def stripe_client():
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"url": "https://checkout.stripe.test/session"})
    return StripeClient("sk_test_abc", SECRET, "https://app.example.invalid", client=httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(scope="module")
def client():
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON, stripe=stripe_client()))


def hdr(u):
    return {"Authorization": f"Bearer {mint_token(h.SECRET, u)}"}


def sign(payload: bytes, secret=SECRET, t=None):
    t = int(time.time()) if t is None else t
    return {"Stripe-Signature": f"t={t},v1=" + hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}


def post_event(client, event, **kw):
    body = json.dumps(event).encode()
    return client.post("/billing/webhook", content=body, headers=sign(body, **kw))


def firm(admin):
    f = h.seed(admin)
    admin.execute("update app_user set is_admin = true where id = %s", (f["designer"],))
    return f


def sub(admin, f):
    return admin.execute("select status, plan_id, seats, max_projects, stripe_customer_id, stripe_subscription_id from subscription where firm_id = %s", (f["firm"],)).fetchone()


# ---- the signature -----------------------------------------------------------------------------------------------------------
def test_signature_verification():
    body = b'{"id":"evt_1"}'
    now = 1_800_000_000
    good = sign(body, t=now)["Stripe-Signature"]
    assert verify_signature(body, good, SECRET, now=now + 10)
    assert not verify_signature(body, good, "whsec_other", now=now)                                      # wrong secret
    assert not verify_signature(body + b" ", good, SECRET, now=now)                                      # tampered body
    assert not verify_signature(body, good, SECRET, now=now + 301)                                       # too old (replay)
    assert not verify_signature(body, good, SECRET, now=now - 301)                                       # from the future
    assert not verify_signature(body, "", SECRET, now=now) and not verify_signature(body, "t=abc,v1=00", SECRET, now=now)
    assert not verify_signature(body, good, "", now=now)
    two = good + ",v1=" + "0" * 64
    assert verify_signature(body, two, SECRET, now=now)                                                  # Stripe may send several signatures


def test_a_live_key_is_refused():
    with pytest.raises(ValueError, match="TEST key"):
        StripeClient("sk_live_abc", SECRET, "https://x")
    with pytest.raises(ValueError):
        StripeClient("rk_live_abc", SECRET, "https://x")


# ---- the webhook -------------------------------------------------------------------------------------------------------------
def test_events_update_the_subscription_once_and_only_when_signed(admin, client):
    f = firm(admin)
    fid = str(f["firm"])
    done = {"id": f"evt_{uid().replace("-", "")}", "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": fid, "customer": "cus_1", "subscription": "sub_" + fid[:8]}}}
    assert post_event(client, done).json()["result"] == "linked"
    assert post_event(client, done).json() == {"received": True, "duplicate": True}
    assert sub(admin, f)[4:] == ("cus_1", "sub_" + fid[:8])
    upd = {"id": f"evt_{uid().replace("-", "")}", "type": "customer.subscription.updated",
           "data": {"object": {"id": "sub_" + fid[:8], "customer": "cus_1", "status": "active", "metadata": {"firm_id": fid, "plan_id": "starter"},
                               "current_period_end": 1_900_000_000, "items": {"data": [{"quantity": 4}]}}}}
    assert post_event(client, upd).json()["result"] == "updated"
    assert sub(admin, f)[:4] == ("active", "starter", 4, 5)
    failed = {"id": f"evt_{uid().replace("-", "")}", "type": "invoice.payment_failed", "data": {"object": {"subscription": "sub_" + fid[:8]}}}
    post_event(client, failed)
    assert sub(admin, f)[0] == "past_due"
    post_event(client, {"id": f"evt_{uid().replace("-", "")}", "type": "invoice.paid", "data": {"object": {"subscription": "sub_" + fid[:8]}}})
    assert sub(admin, f)[0] == "active"
    post_event(client, {"id": f"evt_{uid().replace("-", "")}", "type": "customer.subscription.deleted", "data": {"object": {"id": "sub_" + fid[:8], "metadata": {"firm_id": fid}}}})
    assert sub(admin, f)[0] == "canceled"
    bad = json.dumps({"id": f"evt_{uid().replace("-", "")}", "type": "customer.subscription.deleted", "data": {"object": {}}}).encode()
    assert client.post("/billing/webhook", content=bad, headers=sign(bad, secret="whsec_wrong")).status_code == 400
    assert client.post("/billing/webhook", content=bad).status_code == 400
    assert client.post("/billing/webhook", content=bad, headers=sign(bad, t=int(time.time()) - 3600)).status_code == 400
    unknown = {"id": f"evt_{uid().replace("-", "")}", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_nobody", "status": "active"}}}
    assert post_event(client, unknown).json()["result"] == "ignored"                                         # an unknown subscription changes nothing
    junk = json.dumps({"id": "not an event id", "type": "x"}).encode()
    assert client.post("/billing/webhook", content=junk, headers=sign(junk)).status_code == 400


def test_a_client_cannot_write_a_subscription(admin):
    import psycopg
    f = firm(admin)
    with psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": str(f["designer"])}),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("update subscription set status = 'active'")


# ---- checkout and the firm's view ----------------------------------------------------------------------------------------------
def test_checkout_is_for_administrators_uses_test_prices_and_sends_no_card_data(admin, client, monkeypatch):
    f = firm(admin)
    monkeypatch.setenv("STRIPE_PRICE_STARTER_SEAT", "price_test_seat")
    assert client.post("/billing/checkout", headers=hdr(f["checker"]), json={"plan_id": "starter"}).status_code == 403
    assert client.post("/billing/checkout", headers=hdr(f["designer"]), json={"plan_id": "nope"}).status_code == 422
    captured.clear()
    r = client.post("/billing/checkout", headers=hdr(f["designer"]), json={"plan_id": "starter"})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.test/")
    req = captured[-1]
    form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
    assert req.url.path == "/v1/checkout/sessions" and req.headers["authorization"].startswith("Basic ")
    assert form["client_reference_id"] == str(f["firm"]) and form["line_items[0][price]"] == "price_test_seat" and form["line_items[0][quantity]"] == "2"
    assert form["mode"] == "subscription" and "card" not in req.content.decode().lower()
    monkeypatch.delenv("STRIPE_PRICE_STUDIO_SEAT", raising=False)
    assert client.post("/billing/checkout", headers=hdr(f["designer"]), json={"plan_id": "studio"}).status_code == 503       # the test price is not configured
    assert client.post("/billing/portal", headers=hdr(f["designer"])).status_code == 409                                       # nothing to manage yet


def test_the_overview_reports_status_seats_and_projects(admin, client):
    f = firm(admin)
    ov = client.get("/billing", headers=hdr(f["designer"])).json()
    assert ov["test_mode"] is True and ov["subscription"]["status"] == "trialing" and ov["active_seats"] == 2 and ov["projects"] == 1
    assert {p["id"] for p in ov["plans"]} == {"starter", "studio"}


# ---- the gate on starting projects --------------------------------------------------------------------------------------------
NEW = {"address": "9 New Street, Sydney NSW", "state": "NSW", "ncc_edition": "NCC2025", "climate_zone": 5, "approval_date": "2026-11-01"}


def test_a_trial_or_active_subscription_starts_projects_and_anything_else_is_refused_with_402(admin, client):
    f = firm(admin)
    d = hdr(f["designer"])
    ok = client.post("/projects", headers=d, json=NEW)
    assert ok.status_code == 200 and ok.json()["project_id"] and ok.json()["revision_id"]
    assert admin.execute("select architect_rev from revision where id = %s", (ok.json()["revision_id"],)).fetchone()[0] == "A"
    assert admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'project_created'", (f["firm"],)).fetchone()[0] == 1
    for sql, fragment in (("update subscription set trial_ends_at = now() - interval '1 day' where firm_id = %s", "trial has ended"),
                          ("update subscription set status = 'past_due' where firm_id = %s", "payment failed"),
                          ("update subscription set status = 'canceled' where firm_id = %s", "cancelled")):
        admin.execute(sql, (f["firm"],))
        r = client.post("/projects", headers=d, json=NEW)
        assert r.status_code == 402 and fragment in r.json()["detail"]["message"] and "untouched" in r.json()["detail"]["message"]
    admin.execute("update subscription set status = 'active', max_projects = 3 where firm_id = %s", (f["firm"],))
    assert client.post("/projects", headers=d, json=NEW).status_code == 200                      # the seeded project + the first created + this one = 3
    r = client.post("/projects", headers=d, json=NEW)
    assert r.status_code == 402 and "allows 3 projects" in r.json()["detail"]["message"]


def test_only_a_designer_starts_projects_and_existing_work_stays_reachable_when_billing_lapses(admin, client):
    f = firm(admin)
    assert client.post("/projects", headers=hdr(f["checker"]), json=NEW).status_code == 403
    admin.execute("update subscription set status = 'canceled' where firm_id = %s", (f["firm"],))
    assert client.get(f"/projects/{f['project']}/history", headers=hdr(f["designer"])).status_code == 200            # signed work is never locked
    assert client.get("/projects", headers=hdr(f["designer"])).status_code == 200
    for bad in ({**NEW, "state": "XX"}, {**NEW, "address": "x"}, {**NEW, "ncc_edition": "NCC1999"}, {**NEW, "extra": 1}):
        assert client.post("/projects", headers=hdr(f["designer"]), json=bad).status_code == 422


def test_a_client_cannot_create_a_project_or_revision_around_the_billing_gate(admin):
    import psycopg
    f = firm(admin)
    admin.execute("update subscription set status = 'canceled' where firm_id = %s", (f["firm"],))
    with psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select set_config('request.jwt.claims', %s, false)", (json.dumps({"sub": str(f["designer"])}),))
        for sql in ("insert into project (firm_id, address, state, ncc_edition) values (%s, 'sneaky', 'VIC', 'NCC2025')",
                    "insert into revision (firm_id, project_id, architect_rev) select %s, id, 'Z' from project where firm_id = %s limit 1"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, (f["firm"], f["firm"])[: sql.count("%s")])


def test_events_apply_in_order_one_subscription_at_a_time_and_never_live(admin, client):
    f = firm(admin)
    fid = str(f["firm"])
    s1, s2 = "sub_a" + fid[:8], "sub_b" + fid[:8]
    ev = lambda t, obj, created, live=False: {"id": f"evt_{uid().replace('-', '')}", "type": t, "created": created, "livemode": live, "data": {"object": obj}}
    assert post_event(client, ev("customer.subscription.created", {"id": s1, "customer": "cus_9", "status": "active", "metadata": {"firm_id": fid}}, 1000)).json()["result"] == "updated"
    # an older event arriving late cannot undo a newer one
    assert post_event(client, ev("customer.subscription.updated", {"id": s1, "status": "past_due", "metadata": {"firm_id": fid}}, 900)).json()["result"] == "ignored"
    assert sub(admin, f)[0] == "active"
    # a second subscription id for the same firm is not adopted
    assert post_event(client, ev("customer.subscription.created", {"id": s2, "customer": "cus_9", "status": "canceled", "metadata": {"firm_id": fid}}, 1100)).json()["result"] == "ignored"
    assert sub(admin, f)[0] == "active" and sub(admin, f)[5] == s1
    # the newer API shape of an invoice still reaches the right subscription
    inv = {"parent": {"subscription_details": {"subscription": s1}}}
    assert post_event(client, ev("invoice.payment_failed", inv, 1200)).json()["result"] == "updated" and sub(admin, f)[0] == "past_due"
    # a live-mode event is refused outright
    body = json.dumps(ev("invoice.paid", {"subscription": s1}, 1300, live=True)).encode()
    assert client.post("/billing/webhook", content=body, headers=sign(body)).status_code == 400
    # an oversized body is refused before it is read in full
    big = b"x" * 1_000_001
    assert client.post("/billing/webhook", content=big, headers=sign(big)).status_code == 413


def test_a_firm_with_a_live_subscription_cannot_start_a_second_checkout(admin, client, monkeypatch):
    f = firm(admin)
    monkeypatch.setenv("STRIPE_PRICE_STARTER_SEAT", "price_test_seat")
    admin.execute("update subscription set stripe_subscription_id = 'sub_live1', status = 'active' where firm_id = %s", (f["firm"],))
    r = client.post("/billing/checkout", headers=hdr(f["designer"]), json={"plan_id": "starter"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_subscribed"
