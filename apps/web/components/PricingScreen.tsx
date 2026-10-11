"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { PricingView } from "@/lib/types";

const money = (cents: number, currency: string) => new Intl.NumberFormat("en-AU", { style: "currency", currency: currency.toUpperCase() }).format(cents / 100);

/** Pricing read from the billing configuration (nothing is hard-coded here). */
export function PricingScreen() {
  const [p, setP] = useState<PricingView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { void api.pricing().then(setP).catch((e) => setMessage(e instanceof ApiError ? e.message : "Pricing is not available right now.")); }, []);
  return (
    <main>
      <h1>Pricing</h1>
      <p role="note" data-testid="draft-copy" style={{ border: "2px solid #b45309", padding: 8, fontWeight: 700 }}>{p?.banner ?? "DRAFT COPY"}</p>
      {message && <p role="alert">{message}</p>}
      {p && (
        <>
          <p>{p.trial_days}-day trial. Prices per month, shown in {p.currency.toUpperCase()}.</p>
          <table>
            <thead><tr><th scope="col">Plan</th><th scope="col">Per seat</th><th scope="col">Per project</th><th scope="col">Projects included</th></tr></thead>
            <tbody>{p.plans.map((x) => <tr key={x.id}><th scope="row">{x.name}</th><td>{money(x.seat_cents, p.currency)}</td><td>{x.project_cents ? money(x.project_cents, p.currency) : "included"}</td><td>{x.max_projects ?? "unlimited"}</td></tr>)}</tbody>
          </table>
        </>
      )}
      <p><a href="/welcome">Back</a> | <a href="/login">Sign in</a></p>
    </main>
  );
}
