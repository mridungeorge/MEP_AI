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
  const load = useCallback(async () => {
    try { setData(await api.evidence(revisionId, "pdf")); } catch (e) { setMessage(refusal(e)); }
  }, [revisionId]);
  useEffect(() => { void load(); }, [load]);
  if (!data || data.sources.length === 0) return null;
  return (
    <section aria-label="Evidence from drawings" data-testid="evidence">
      <h2>Read from drawings (evidence, not inputs)</h2>
      <p>These values were read by a vision model from PDF pages. They are not used by any rule. Check each against the drawing before you add it.</p>
      {data.sources.map((s) => (
        <div key={s.sha256}>
          <h3>{s.name}</h3>
          {s.problems.length > 0 && <ul>{s.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
          {s.candidates.length === 0 ? <p>Nothing was read from this file.</p> : (
            <table>
              <thead><tr><th>Name</th><th>Area (m2)</th><th>Use</th><th>Storey</th><th>Confidence</th><th /></tr></thead>
              <tbody>{s.candidates.map((c) => (
                <tr key={c.key} data-testid="evidence-row">
                  <td>{String(c.name ?? "")}</td><td>{String(c.area_m2 ?? "")}</td><td>{String(c.use ?? "")}</td><td>{String(c.storey ?? "")}</td>
                  <td>{c.confidence === null ? "?" : `${Math.round(Number(c.confidence) * 100)}%`}</td>
                  <td>{canEdit && typeof c.name === "string" && typeof c.area_m2 === "number" && (
                    <button type="button" onClick={async () => {
                      try {
                        await api.addSpace(revisionId, { name: c.name as string, area_m2: c.area_m2 as number, use: (c.use as string) ?? null,
                                                          storey: (c.storey as string) ?? null, manual_trace: true });
                        setMessage(`${c.name} added as a space you entered by hand. Confirm it at Gate 1.`);
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
