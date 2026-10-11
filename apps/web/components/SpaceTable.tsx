"use client";
import { EmptyState } from "@/components/EmptyState";
import { useState } from "react";
import { rowState } from "@/lib/gate1";
import type { SpaceInput, SpaceRow } from "@/lib/types";
import { ProvenanceBadge } from "./ProvenanceBadge";

type Field = "name" | "area_m2" | "use" | "storey" | "ceiling_void_mm";
const NUMERIC: Field[] = ["area_m2", "ceiling_void_mm"];   // a storey is a name in IFC ("00 groundfloor")

function parse(field: Field, raw: string): string | number | null {
  if (raw.trim() === "") return null;
  return NUMERIC.includes(field) ? Number(raw) : raw;
}

export function SpaceTable({
  spaces, selected, onToggle, onEdit, onRemoveEvidence,
}: {
  spaces: SpaceRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  /** Persist an edit; the parent resets the row to unconfirmed. */
  onEdit: (id: string, patch: SpaceInput) => void;
  /** Remove a space that was made from a drawing reading (wrong unit, wrong candidate) so it can be added again. */
  onRemoveEvidence?: (id: string) => void;
}) {
  const cell = (s: SpaceRow, f: Field) => (
    <input
      aria-label={f}
      defaultValue={s[f] ?? ""}
      type={NUMERIC.includes(f) ? "number" : "text"}
      step="any"
      style={{ width: f === "name" ? 140 : 80 }}
      onBlur={(e) => {
        const next = parse(f, e.target.value);
        if (next !== s[f]) onEdit(s.id, { [f]: next } as SpaceInput);
      }}
    />
  );
  return (
    <section aria-label="Spaces">
      <h2>Spaces</h2>
      {spaces.length === 0 && <EmptyState title="No spaces yet." next="Upload an architect model above, or add a space by manual trace, then confirm each one." />}
      <table>
        <thead>
          <tr>
            <th>Select</th><th>IFC GUID</th><th>Name</th><th>Area (m2)</th><th>Use</th>
            <th>Storey</th><th>Ceiling void (mm)</th><th>Provenance</th>
          </tr>
        </thead>
        <tbody>
          {spaces.map((s) => (
            // key includes provenance so uncontrolled inputs refresh after server round-trips
            <tr key={`${s.id}:${s.provenance}`} data-state={rowState(s)}>
              <td><input type="checkbox" checked={selected.has(s.id)} onChange={() => onToggle(s.id)} aria-label={`Select ${s.name ?? s.id}`} /></td>
              <td>{s.evidence ? <em>from drawing p.{s.evidence.page ?? "?"} ({s.evidence.area_unit})</em> : s.manual_trace ? <em>manual trace</em> : s.ifc_guid}</td>
              <td>{cell(s, "name")}</td>
              <td>{cell(s, "area_m2")}</td>
              <td>{cell(s, "use")}</td>
              <td>{cell(s, "storey")}</td>
              <td>{cell(s, "ceiling_void_mm")}</td>
              <td><ProvenanceBadge provenance={s.provenance} />
                {s.evidence && onRemoveEvidence && <> <button type="button" onClick={() => onRemoveEvidence(s.id)}>Remove (read from a drawing)</button></>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

/** Manual trace fallback: designer enters name, area and storey by hand. */
export function ManualTraceForm({ onAdd }: { onAdd: (s: SpaceInput) => Promise<void> }) {
  const [name, setName] = useState("");
  const [area, setArea] = useState("");
  const [storey, setStorey] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const a = Number(area);
    const st = storey.trim();
    if (!name.trim() || !(a > 0) || area === "" || st === "") {
      return setError("Enter a name, an area above 0 m2 and a storey.");
    }
    setError(null);
    try {
      await onAdd({ name: name.trim(), area_m2: a, storey: st, manual_trace: true, ifc_guid: null });
      setName(""); setArea(""); setStorey("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section aria-label="Manual trace">
      <h3>Manual trace (ingest health is low)</h3>
      <input aria-label="Name" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />{" "}
      <input aria-label="Area (m2)" placeholder="Area (m2)" type="number" step="any" value={area} onChange={(e) => setArea(e.target.value)} />{" "}
      <input aria-label="Storey" placeholder="Storey" value={storey} onChange={(e) => setStorey(e.target.value)} />{" "}
      <button type="button" onClick={submit}>Add manual trace space</button>
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
