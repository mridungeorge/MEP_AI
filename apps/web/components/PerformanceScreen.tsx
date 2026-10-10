"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api, getToken } from "@/lib/api";
import type { PerfOverview } from "@/lib/types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function download(revisionId: string, ext: "json" | "xlsx") {
  const token = await getToken();
  const res = await fetch(`${BASE_URL}/revisions/${revisionId}/performance/package.${ext}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = `performance-solution-start.${ext}`; a.click();
  URL.revokeObjectURL(url);
}

/** Failed DTS results: the engine's "Performance Solution pathway likely" flag, the pathway the designer pursues, the engineer's own results as evidence (never a rule
 *  result), and the starting data package. */
export function PerformanceScreen({ revisionId }: { revisionId: string }) {
  const [data, setData] = useState<PerfOverview | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [form, setForm] = useState<Record<string, { title: string; tool: string; metrics: string; file: File | null }>>({});
  const load = useCallback(async () => { try { setData(await api.performance(revisionId)); } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); } }, [revisionId]);
  useEffect(() => { void load(); }, [load]);
  const act = async (fn: () => Promise<unknown>, ok: string) => { setMessage(null); try { await fn(); setMessage(ok); await load(); } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); } };
  if (!data) return <main><h1>Performance Solution</h1><p>{message ?? "Loading…"}</p></main>;
  return (
    <main>
      <h1>Performance Solution pathway</h1>
      <p role="note" data-testid="perf-banner" style={{ border: "2px solid #b45309", padding: 8 }}>{data.banner}</p>
      <p><button type="button" onClick={() => void download(revisionId, "xlsx")}>Download starting data package (XLSX)</button>{" "}
         <button type="button" onClick={() => void download(revisionId, "json")}>Download (JSON)</button></p>
      {message && <p role="status">{message}</p>}
      {data.results.length === 0 && <p>No failed results: nothing needs a Performance Solution.</p>}
      {data.results.map((r) => {
        const key = `${r.subject_id}|${r.rule_id}`;
        const f = form[key] ?? { title: "", tool: "", metrics: "[]", file: null };
        return (
          <section key={key} aria-label={`${r.subject_id} ${r.rule_id}`}>
            <h2>{r.subject_id}: {r.rule_id} {r.clause ? `(${r.clause})` : ""}</h2>
            {r.flag && <p><strong data-testid="perf-flag">{r.flag}</strong>: no single change to an input the engineer decides makes this rule pass without breaking another.</p>}
            <label>Pathway{" "}
              <select aria-label={`Pathway for ${r.rule_id}`} value={r.pathway} onChange={(e) => void act(() => api.setPathway(revisionId, r.subject_id, r.rule_id, e.target.value), "Pathway saved.")}>
                <option value="DTS">DTS (deemed-to-satisfy)</option><option value="PERFORMANCE_SOLUTION">Performance Solution</option>
              </select></label>
            <h3>Evidence you supply (not a rule result)</h3>
            <ul>{r.evidence.map((e) => <li key={e.id}>{e.title}{e.tool ? ` (${e.tool})` : ""}: {e.metrics.map((m) => `${m.name} ${m.value} ${m.unit}`).join(", ") || "no figures"}{e.file_name ? ` — file ${e.file_name}` : ""}</li>)}</ul>
            <input aria-label={`Evidence title ${r.rule_id}`} placeholder="title" value={f.title} onChange={(e) => setForm({ ...form, [key]: { ...f, title: e.target.value } })} />{" "}
            <input aria-label={`Tool ${r.rule_id}`} placeholder="tool (e.g. IES VE)" value={f.tool} onChange={(e) => setForm({ ...form, [key]: { ...f, tool: e.target.value } })} />{" "}
            <input aria-label={`Metrics ${r.rule_id}`} size={50} placeholder={'[{"name":"peak temperature","value":26.4,"unit":"degC"}]'} value={f.metrics} onChange={(e) => setForm({ ...form, [key]: { ...f, metrics: e.target.value } })} />{" "}
            <input type="file" aria-label={`Evidence file ${r.rule_id}`} onChange={(e) => setForm({ ...form, [key]: { ...f, file: e.target.files?.[0] ?? null } })} />{" "}
            <button type="button" disabled={f.title.trim().length < 3} onClick={() => void act(() => api.addPerfEvidence(revisionId, r.subject_id, r.rule_id, { title: f.title.trim(), tool: f.tool, metrics: f.metrics, file: f.file }), "Evidence recorded.")}>Record evidence</button>
          </section>
        );
      })}
    </main>
  );
}
