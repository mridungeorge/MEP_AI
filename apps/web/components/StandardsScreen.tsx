"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { StandardSlot } from "@/lib/types";

/** The licensed standards the product can take rule packs for later. Every slot is empty and says why. */
export function StandardsScreen() {
  const [slots, setSlots] = useState<StandardSlot[] | null>(null);
  const [note, setNote] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { void api.standards().then((r) => { setSlots(r.slots); setNote(r.note); }).catch((e) => setMessage(e instanceof ApiError ? e.message : String(e))); }, []);
  return (
    <main>
      <h1>Licensed standards</h1>
      <p>{note}</p>
      {message && <p role="alert">{message}</p>}
      <table>
        <thead><tr><th>Standard</th><th>Discipline</th><th>State</th><th>Rules loaded</th></tr></thead>
        <tbody>{slots?.map((s) => (
          <tr key={s.id} data-state={s.state}>
            <td><strong>{s.standard}</strong><br />{s.title}</td><td>{s.discipline}</td>
            <td><span data-testid={`state-${s.id}`}>{s.state.replace("_", " ")}</span><br />{s.message}</td><td>{s.rules_loaded}</td>
          </tr>))}</tbody>
      </table>
    </main>
  );
}
