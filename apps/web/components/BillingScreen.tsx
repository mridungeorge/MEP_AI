"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { BillingOverview } from "@/lib/types";

const money = (cents: number, cur: string) => new Intl.NumberFormat("en-AU", { style: "currency", currency: cur.toUpperCase() }).format(cents / 100);

/** The firm's subscription (Stripe TEST mode: no real money moves). Only an administrator manages it. */
export function BillingScreen() {
  const [b, setB] = useState<BillingOverview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { api.billing().then(setB).catch((e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e))); }, []);
  const go = async (fn: () => Promise<{ url: string }>) => {
    setMessage(null);
    try { window.location.assign((await fn()).url); } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); }
  };
  if (!b) return <main><h1>Billing</h1><p>{message ?? "Loading…"}</p></main>;
  const s = b.subscription;
  return (
    <main>
      <h1>Billing</h1>
      <p role="note" data-testid="test-mode" style={{ border: "2px solid #b45309", padding: 8 }}>STRIPE TEST MODE: use Stripe&apos;s test cards. No real payment is taken.</p>
      {s && <p data-testid="subscription">Status: <strong>{s.status}</strong>, plan {s.plan_id}. {s.status === "trialing" ? `Trial ends ${s.trial_ends_at.slice(0, 10)}.` : ""} {b.active_seats} active seat(s), {b.projects} project(s).</p>}
      <p>If the subscription lapses you can still open, review and sign existing projects; only starting a NEW project is paused.</p>
      <ul>{b.plans.map((p) => (
        <li key={p.id}><strong>{p.name}</strong>: {money(p.seat_cents, b.currency)} per seat per month{p.project_cents ? `, ${money(p.project_cents, b.currency)} per project` : ""}, {p.max_projects ? `up to ${p.max_projects} projects` : "unlimited projects"}{" "}
          <button type="button" disabled={!b.available} onClick={() => void go(() => api.billingCheckout(p.id))}>Subscribe (test)</button></li>
      ))}</ul>
      {s?.has_customer && <button type="button" onClick={() => void go(() => api.billingPortal())}>Manage subscription</button>}
      {!b.available && <p>Billing is not configured on this server.</p>}
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
