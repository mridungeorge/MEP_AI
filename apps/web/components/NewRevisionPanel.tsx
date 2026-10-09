"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";

/** The architect re-issued the model: upload it against the FROZEN revision. The server never changes a frozen revision; it makes a
 *  child revision (the services are copied with their confirmations), ingests the file into it, and the diff is reviewed there. */
export function NewRevisionPanel({ projectId, revisionId }: { projectId: string; revisionId: string }) {
  const router = useRouter();
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(file: File) {
    setBusy(true);
    setError(null);
    try {
      const r = await api.upload(revisionId, file, label.trim() || undefined);
      if (r.new_revision) router.push(`/projects/${projectId}/revisions/${r.new_revision.id}/diff`);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.code ?? e.status}: ${e.message}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="New architect revision">
      <h2>Upload a new architect revision</h2>
      <p>This revision is frozen. A new model becomes a new revision; nothing here is changed.</p>
      <label>
        Architect revision label (optional){" "}
        <input value={label} maxLength={20} onChange={(e) => setLabel(e.target.value)} aria-label="Architect revision label" />
      </label>{" "}
      <input type="file" aria-label="Upload new revision file" disabled={busy}
             onChange={(e) => { const f = e.target.files?.[0]; if (f) void send(f); e.target.value = ""; }} />
      {busy && <p>Reading the file…</p>}
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
