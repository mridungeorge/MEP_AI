"use client";
import { HelpTip } from "@/components/HelpTip";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { CommissioningImport } from "@/lib/types";

/** Commissioning sheets from the signed revision (one row per terminal, blank measured columns) and the re-import of what was measured on site. Readings are records, not results. */
export function CommissioningPanel({ revisionId }: { revisionId: string }) {
  const [tolerance, setTolerance] = useState("");
  const [result, setResult] = useState<CommissioningImport | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fail = (e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e));
  const save = (kind: "xlsx" | "pdf") => { setMessage(null); void api.commissioningFile(revisionId, kind).then((b) => {
    const a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = `commissioning.${kind}`; a.click(); }).catch(fail); };
  const tol = Number(tolerance);
  const valid = tolerance.trim() !== "" && Number.isFinite(tol) && tol >= 0 && tol <= 50;
  return (
    <section aria-label="Commissioning">
      <h2>Commissioning sheets</h2>
      <p><HelpTip topic="commissioning" label="About commissioning" /></p>
      <p>One row per terminal with its design airflow from the signed revision. Measured columns are blank for the technician.</p>
      <button type="button" onClick={() => save("xlsx")}>Download XLSX</button>{" "}<button type="button" onClick={() => save("pdf")}>Download PDF</button>
      <h3>Import measured values</h3>
      <label>Tolerance (%, you decide it; not assumed){" "}<input aria-label="Tolerance percent" size={4} value={tolerance} onChange={(e) => setTolerance(e.target.value)} /></label>{" "}
      <input type="file" accept=".xlsx" aria-label="Filled-in sheet" disabled={!valid}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) { setMessage(null); void api.importCommissioning(revisionId, f, tol).then(setResult).catch(fail); } }} />
      {message && <p role="alert">{message}</p>}
      {result && (
        <div>
          <p>{Object.entries(result.counts).map(([k, v]) => `${k.replaceAll("_", " ").toLowerCase()}: ${v}`).join(", ")}. {result.note}</p>
          <table><thead><tr><th>Terminal</th><th>Design L/s</th><th>Measured L/s</th><th>Variance %</th><th>Flag</th></tr></thead>
            <tbody>{result.readings.map((r) => <tr key={r.terminal} data-flag={r.flag}><td>{r.terminal}</td><td>{r.design_ls ?? "—"}</td><td>{r.measured_ls ?? "—"}</td><td>{r.variance_pct ?? "—"}</td><td>{r.flag.replaceAll("_", " ")}</td></tr>)}</tbody></table>
        </div>
      )}
    </section>
  );
}
