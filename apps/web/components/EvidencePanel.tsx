"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { EvidenceResponse } from "@/lib/types";

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

/** What was read from PDF drawings by the vision model. This is EVIDENCE only: it is not an input to any rule and never becomes a space by
 *  itself. A designer may turn a candidate into a space of their own (marked as hand-entered); it then needs Gate 1 like any other. */
export function EvidencePanel({ revisionId, canEdit, onChanged }: { revisionId: string; canEdit: boolean; onChanged: () => Promise<void> }) {
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [units, setUnits] = useState<Record<string, { area?: string; void?: string }>>({});
  const load = useCallback(async () => {
    try { setData(await api.evidence(revisionId, "pdf")); } catch (e) { setMessage(refusal(e)); }
  }, [revisionId]);
  useEffect(() => { void load(); }, [load]);
  if (!data || data.sources.length === 0) return null;
  return (
    <section aria-label="Evidence from drawings" data-testid="evidence">
      <h2>Read from drawings (evidence, not inputs)</h2>
      <p>These values were read by a vision model from PDF pages. They are not used by any rule. No unit is assumed: choose the unit printed on the drawing for each value before you add it. The space stays extracted and needs Gate 1.</p>
      {data.sources.map((s) => (
        <div key={s.sha256}>
          <h3>{s.name}</h3>
          {s.problems.length > 0 && <ul>{s.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          {s.candidates.length === 0 ? <p>Nothing was read from this file.</p> : (
            <table>
              <thead><tr><th>Name</th><th>Area as read</th><th>Unit on the drawing</th><th>Use</th><th>Storey</th><th>Confidence</th><th /></tr></thead>
              <tbody>{s.candidates.map((c) => (
                <tr key={c.key} data-testid="evidence-row">
                  <td>{String(c.name ?? "")}</td><td>{String(c.area ?? "")} {c.area_unit_shown ? <small>({String(c.area_unit_shown)} printed)</small> : null}</td>
                  <td>{typeof c.area === "number" && (
                    <select aria-label={`Area unit for ${String(c.name ?? c.key)}`} value={units[c.key]?.area ?? ""}
                            onChange={(e) => setUnits((u) => ({ ...u, [c.key]: { ...u[c.key], area: e.target.value } }))}>
                      <option value="">choose…</option><option value="m^2">m²</option><option value="ft^2">ft²</option><option value="mm^2">mm²</option>
                    </select>)}
                    {typeof c.ceiling_void === "number" && (
                      <> void {String(c.ceiling_void)}{" "}
                      <select aria-label={`Void unit for ${String(c.name ?? c.key)}`} value={units[c.key]?.void ?? ""}
                              onChange={(e) => setUnits((u) => ({ ...u, [c.key]: { ...u[c.key], void: e.target.value } }))}>
                        <option value="">choose…</option><option value="mm">mm</option><option value="m">m</option><option value="in">in</option><option value="ft">ft</option>
                      </select></>)}
                  </td><td>{String(c.use ?? "")}</td><td>{String(c.storey ?? "")}</td>
                  <td>{c.confidence === null ? "?" : `${Math.round(Number(c.confidence) * 100)}%`}</td>
                  <td>{canEdit && typeof c.name === "string" && typeof c.area === "number" && (
                    <button type="button" disabled={!units[c.key]?.area || (typeof c.ceiling_void === "number" && !units[c.key]?.void)} onClick={async () => {
                      try {
                        await api.addEvidenceSpace(revisionId, { source_sha256: s.sha256, entity_key: c.key, area_unit: units[c.key]!.area!,
                                                                  ...(typeof c.ceiling_void === "number" ? { void_unit: units[c.key]!.void } : {}) });
                        setMessage(`${c.name} added as a space read from the drawing (still extracted). Confirm it at Gate 1.`);
                        await onChanged();
                      } catch (e) { setMessage(refusal(e)); }
                    }}>Add as a space</button>
                  )}</td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </div>
      ))}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
