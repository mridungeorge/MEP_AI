"use client";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";

export const STATES = ["NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"];

export function NewProjectForm({ onCreated, defaultOpen = false }: { onCreated: (r: { project_id: string; revision_id: string }) => void; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const [address, setAddress] = useState("");
  const [state, setState] = useState("VIC");
  const [edition, setEdition] = useState("NCC2025");
  const [zone, setZone] = useState("");
  const [date, setDate] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  if (!open) return <p><button type="button" onClick={() => setOpen(true)}>Start a project</button></p>;
  return (
    <section aria-label="Start a project">
      <h2>Start a project</h2>
      <label>Address <input aria-label="Project address" value={address} onChange={(e) => setAddress(e.target.value)} size={50} /></label>{" "}
      <label>State <select aria-label="New project state" value={state} onChange={(e) => setState(e.target.value)}>{STATES.map((s) => <option key={s}>{s}</option>)}</select></label>{" "}
      <label>NCC edition <select aria-label="New project edition" value={edition} onChange={(e) => setEdition(e.target.value)}><option>NCC2022</option><option>NCC2025</option></select></label>{" "}
      <label>Climate zone <input aria-label="Climate zone" type="number" min={1} max={8} value={zone} onChange={(e) => setZone(e.target.value)} style={{ width: 50 }} /></label>{" "}
      <label>Building approval date <input aria-label="Approval date" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>{" "}
      <button type="button" disabled={address.trim().length < 3} onClick={() => {
        setMessage(null);
        api.createProject({ address: address.trim(), state, ncc_edition: edition, ...(zone ? { climate_zone: Number(zone) } : {}), ...(date ? { approval_date: date } : {}) })
          .then(onCreated).catch((e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e)));
      }}>Create</button>
      {message && <p role="alert">{message}</p>}
    </section>
  );
}
