"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { UploadResponse, VisionJob } from "@/lib/types";

/** Upload an IFC or DXF. The server decides the type from the file's content and refuses anything else; what it reads
 *  lands as 'extracted' (not confirmed) and the ingest health score is shown as the API computed it. */
export function UploadPanel({ revisionId, onUploaded }: { revisionId: string; onUploaded: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<VisionJob[]>([]);

  const loadJobs = useCallback(async () => {
    try { setJobs(await api.visionJobs(revisionId)); } catch { /* the upload itself reports its errors */ }
  }, [revisionId]);
  useEffect(() => { void loadJobs(); }, [loadJobs]);
  const active = jobs.some((j) => j.status === "queued" || j.status === "running");
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => {
      void api.visionJobs(revisionId).then(async (next) => {
        const finished = next.some((j) => j.status === "done" || j.status === "failed") && !next.some((j) => j.status === "queued" || j.status === "running");
        setJobs(next);
        if (finished) await onUploaded();
      }).catch(() => undefined);
    }, 2000);
    return () => clearInterval(t);
  }, [active, revisionId, onUploaded]);

  async function send(file: File) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setDone(await api.upload(revisionId, file));
      await loadJobs();
      await onUploaded();
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code ?? e.status}: ${e.message}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Upload model">
      <h2>Upload architect model</h2>
      <p>An IFC (preferred) or DXF plan, or a PDF drawing (read as evidence only), up to 50 MiB. The file type is checked from its content.</p>
      <input
        type="file"
        aria-label="Upload IFC or DXF"
        disabled={busy}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void send(f);
          e.target.value = "";
        }}
      />
      {busy && <p>Reading the file…</p>}
      {done && (
        <p role="status" data-testid="upload-result">
          Uploaded {done.name} as {done.kind.toUpperCase()}: {done.kind === "pdf" ? "it is being read in the background (see below); what is read is evidence, not inputs" : `${done.spaces} space(s) read`}
          {done.health ? `, ingest health ${done.health.score_percent}%` : ""}.
        </p>
      )}
      {done?.new_revision && (
        <p role="status" data-testid="new-revision">
          This revision is frozen, so a new revision (Rev {done.new_revision.architect_rev}) was created.{" "}
          <a href={`/projects/${done.new_revision.project_id}/revisions/${done.new_revision.id}/diff`}>Review the diff</a>
        </p>
      )}
      {jobs.length > 0 && (
        <ul data-testid="vision-jobs" aria-label="Drawings being read">
          {jobs.map((j) => (
            <li key={j.id} data-status={j.status}>
              {j.name}: <strong>{j.status}</strong>
              {j.status === "done" ? ` (${j.extractions ?? 0} value(s) read as evidence)` : null}
              {j.status === "failed" && j.error ? ` — ${j.error}` : null}
              {j.status === "done" && j.problems.length > 0 ? ` — ${j.problems[0]}` : null}
            </li>
          ))}
        </ul>
      )}
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
