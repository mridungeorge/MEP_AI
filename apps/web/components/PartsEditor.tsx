"use client";
import { useState } from "react";
import { validatePart } from "@/lib/gate1";
import { BUILDING_CLASSES, type BuildingClass, type BuildingPart } from "@/lib/types";

type Draft = Omit<BuildingPart, "id">;

export function PartsEditor({
  parts, onSave,
}: { parts: BuildingPart[]; onSave: (parts: Draft[]) => Promise<void> }) {
  const [rows, setRows] = useState<Draft[]>(parts.map(({ id: _id, ...r }) => r));
  const [error, setError] = useState<string | null>(null);

  const set = (i: number, patch: Partial<Draft>) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  async function save() {
    for (const r of rows) {
      const e = validatePart(r);
      if (e) return setError(e);
    }
    setError(null);
    try {
      await onSave(rows);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section aria-label="Building parts">
      <h2>Building parts</h2>
      <table>
        <thead>
          <tr><th>Class</th><th>Storeys (1-200)</th><th>Area (m2)</th><th /></tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td>
                <select value={r.building_class} onChange={(e) => set(i, { building_class: e.target.value as BuildingClass })}>
                  {BUILDING_CLASSES.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </td>
              <td><input type="number" min={1} max={200} step={1} value={r.storeys} onChange={(e) => set(i, { storeys: Number(e.target.value) })} /></td>
              <td><input type="number" min={0} step="any" value={r.area_m2} onChange={(e) => set(i, { area_m2: Number(e.target.value) })} /></td>
              <td><button type="button" onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>Remove</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <button type="button" onClick={() => setRows((rs) => [...rs, { building_class: "2", storeys: 1, area_m2: 1 }])}>Add part</button>{" "}
      <button type="button" onClick={save}>Save parts</button>
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
