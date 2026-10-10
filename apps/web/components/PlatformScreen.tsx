"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { PendingRegistration } from "@/lib/types";

/** Platform administrators only: registration numbers submitted by firms, waiting to be checked against the public register. */
export function PlatformScreen() {
  const [rows, setRows] = useState<PendingRegistration[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const load = useCallback(async () => {
    try { setRows(await api.platformRegistrations()); } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const decide = async (id: string, verify: boolean) => {
    setMessage(null);
    try { await api.platformDecide(id, verify, notes[id] ?? ""); await load(); } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); }
  };
  if (!rows) return <main><h1>Registrations to verify</h1><p role="alert">{message ?? "Loading…"}</p></main>;
  return (
    <main>
      <h1>Registrations to verify</h1>
      {message && <p role="alert">{message}</p>}
      {rows.length === 0 && <p>Nothing is waiting.</p>}
      <ul data-testid="platform-queue">{rows.map((r) => (
        <li key={r.id}>
          <strong>{r.firm}</strong>: {r.user_email} claims {r.number} on {r.register}{r.state_scheme ? ` (${r.state_scheme})` : ""}. Submitted by {r.submitted_by_email}.<br />
          Their evidence: {r.evidence}<br />
          <textarea aria-label={`Your note for ${r.number}`} rows={2} cols={60} placeholder="What you checked on the register, where and when" value={notes[r.id] ?? ""} onChange={(e) => setNotes((n) => ({ ...n, [r.id]: e.target.value }))} /><br />
          <button type="button" onClick={() => void decide(r.id, true)}>Verified against the register</button>{" "}
          <button type="button" onClick={() => void decide(r.id, false)}>Reject</button>
        </li>
      ))}</ul>
    </main>
  );
}
