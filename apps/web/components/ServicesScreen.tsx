"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, getToken } from "@/lib/api";
import type { ClashView, Quantities, ServiceItem, SizingView, VoidView } from "@/lib/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function download(path: string, name: string) {
  const token = await getToken();
  const res = await fetch(`${BASE_URL}${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/** Proposed services (ducts, fittings, terminals), the ceiling-void check, quantities and clash-lite. Warnings only: nothing here is a compliance result. */
export function ServicesScreen({ revisionId }: { revisionId: string }) {
  const [items, setItems] = useState<ServiceItem[]>([]);
  const [voids, setVoids] = useState<VoidView | null>(null);
  const [qty, setQty] = useState<Quantities | null>(null);
  const [clash, setClash] = useState<ClashView | null>(null);
  const [sizing, setSizing] = useState<SizingView | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [form, setForm] = useState({ tag: "", width: "600", depth: "300", length: "4", insulation: "25" });
  const [discipline, setDiscipline] = useState("fire");
  const fail = (e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e));
  const load = useCallback(async () => {
    try {
      const [i, v, q, c, z] = await Promise.all([api.services(revisionId), api.ceilingVoid(revisionId), api.quantities(revisionId), api.clash(revisionId), api.sizing(revisionId)]);
      setItems(i.items); setVoids(v); setQty(q); setClash(c); setSizing(z);
    } catch (e) { fail(e); }
  }, [revisionId]);
  useEffect(() => { void load(); }, [load]);
  const act = async (fn: () => Promise<unknown>) => { setMessage(null); try { await fn(); await load(); } catch (e) { fail(e); } };
  return (
    <main>
      <h1>Services, ceiling void and clashes</h1>
      <p role="note">Warnings only. Nothing on this page is a compliance result.</p>
      {message && <p role="alert">{message}</p>}

      <section aria-label="Schedule">
        <h2>Proposed ducts</h2>
        <table><thead><tr><th>Tag</th><th>Kind</th><th>Size (mm)</th><th>Length (m)</th><th>Insulation (mm)</th><th /></tr></thead>
          <tbody>{items.map((i) => (
            <tr key={i.id}><td>{i.tag}</td><td>{i.kind}</td>
              <td>{i.shape === "rect" ? `${i.width_mm} x ${i.depth_mm}` : i.shape === "round" ? `dia ${i.diameter_mm}` : i.fitting_type ?? ""}</td>
              <td>{i.length_m ?? ""}</td><td>{i.insulation_mm}</td>
              <td><button type="button" onClick={() => void act(() => api.deleteService(revisionId, i.id))}>Remove</button></td></tr>))}</tbody></table>
        <form onSubmit={(e) => { e.preventDefault(); void act(() => api.addService(revisionId, { kind: "duct", tag: form.tag, shape: "rect", width: Number(form.width), depth: Number(form.depth), length: Number(form.length), insulation: Number(form.insulation) })); }}>
          {(["tag", "width", "depth", "length", "insulation"] as const).map((k) => (
            <label key={k}>{k}{" "}<input aria-label={`duct ${k}`} size={k === "tag" ? 10 : 5} value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} /></label>))}
          <button type="submit" disabled={form.tag.trim() === ""}>Add duct</button>
        </form>
      </section>

      <section aria-label="Sizing">
        <h2>Duct sizing (equal friction)</h2>
        <p>{sizing?.note}</p>
        <table><thead><tr><th>Duct</th><th>Airflow (L/s)</th><th>Recommended</th><th>Velocity (m/s)</th><th>Velocity limit</th></tr></thead>
          <tbody>{sizing?.ducts.map((d) => (
            <tr key={d.id}><td>{d.tag}</td><td>{d.airflow_ls ?? "—"}</td>
              <td>{d.recommended ? (d.recommended.shape === "rect" ? `${d.recommended.width_mm} x ${d.recommended.depth_mm}` : `dia ${d.recommended.diameter_mm}`) : d.reason ?? "—"}{d.recommended?.notes.length ? ` (${d.recommended.notes.join("; ")})` : ""}</td>
              <td>{d.recommended?.velocity_ms ?? "—"}</td><td>{d.velocity_note ?? "—"}</td></tr>))}</tbody></table>
        <ul aria-label="Airflow balance">{sizing?.balance.map((b) => <li key={b.system_tag}>{b.system_tag}: trunk {b.trunk_ls} L/s, terminals {b.terminals_ls} L/s: {b.status}{b.difference_pct !== null ? ` (${b.difference_pct} %)` : ""}</li>)}</ul>
        <ul aria-label="Reducer spec card drafts">{sizing?.spec_card_drafts.map((c) => <li key={c.mark}>{c.mark}: reducer {c.from_duct} to {c.to_duct}. Needs from an engineer: {c.missing_engineer_inputs.join(", ")}.</li>)}</ul>
      </section>

      <section aria-label="Ceiling void">
        <h2>Ceiling void (clearance {voids?.clearance_mm ?? "…"} mm, set by your firm administrator)</h2>
        <table><thead><tr><th>Space</th><th>Void (mm)</th><th>Needed (mm)</th><th>Margin (mm)</th><th>Status</th></tr></thead>
          <tbody>{voids?.spaces.map((s) => (
            <tr key={s.space_id} data-status={s.status}><td>{s.space}</td><td>{s.ceiling_void_mm ?? "—"}</td><td>{s.required_mm ?? "—"}</td><td>{s.margin_mm ?? "—"}</td>
              <td>{s.status}{s.reason ? ` (${s.reason})` : ""}</td></tr>))}</tbody></table>
        {voids && <p>{voids.note}</p>}
      </section>

      <section aria-label="Quantities">
        <h2>Quantities</h2>
        <p><button type="button" onClick={() => void download(`/revisions/${revisionId}/quantities.csv`, "quantities.csv")}>CSV</button>{" "}
           <button type="button" onClick={() => void download(`/revisions/${revisionId}/quantities.xlsx`, "quantities.xlsx")}>XLSX</button></p>
        <ul>
          {qty?.duct_by_size.map((d) => <li key={`${d.shape}${d.size_mm}`}>{d.shape} {d.size_mm}: {d.count} off, {d.length_m} m, {d.surface_area_m2} m2</li>)}
          {qty?.insulation.map((d) => <li key={d.thickness_mm}>Insulation {d.thickness_mm} mm: {d.area_m2} m2</li>)}
          {qty?.fittings.map((d) => <li key={d.type}>{d.type}: {d.quantity}</li>)}
          {qty?.terminals_per_space.map((d) => <li key={d.space}>Terminals in {d.space}: {d.quantity}</li>)}
        </ul>
      </section>

      <section aria-label="Clash-lite">
        <h2>Clash-lite against other disciplines</h2>
        <label>Discipline{" "}<select value={discipline} onChange={(e) => setDiscipline(e.target.value)}>
          <option value="electrical">Electrical</option><option value="hydraulic">Hydraulic</option><option value="fire">Fire</option></select></label>{" "}
        <input type="file" accept=".ifc" aria-label="IFC model" onChange={(e) => { const f = e.target.files?.[0]; if (f) void act(() => api.uploadClashModel(revisionId, discipline, f)); }} />
        {clash && (
          <>
            <p>{clash.models.length} model(s) loaded; {clash.ducts_checked} duct(s) checked, {clash.ducts_without_coordinates} without coordinates.{" "}
              <button type="button" onClick={() => void download(`/revisions/${revisionId}/clash.bcfzip`, "clash-lite.bcfzip")}>Export BCF 2.1</button></p>
            <ul>{clash.clashes.map((c, n) => <li key={n}>WARNING {c.kind}: duct {c.duct_tag} and {c.discipline} {c.element_class} {c.element_name ?? ""} ({c.gap_mm} mm)</li>)}</ul>
            <p>{clash.note}</p>
          </>
        )}
      </section>
    </main>
  );
}
