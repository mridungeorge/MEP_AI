"use client";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { UploadResponse } from "@/lib/types";

/** Upload an IFC or DXF. The server decides the type from the file's content and refuses anything else; what it reads
 *  lands as 'extracted' (not confirmed) and the ingest health score is shown as the API computed it. */
export function UploadPanel({ revisionId, onUploaded }: { revisionId: string; onUploaded: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function send(file: File) {
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      setDone(await api.upload(revisionId, file));
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
      <p>An IFC (preferred) or DXF plan, up to 50 MiB. The file type is checked from its content.</p>
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
          Uploaded {done.name} as {done.kind.toUpperCase()}: {done.spaces} space(s) read
          {done.health ? `, ingest health ${done.health.score_percent}%` : ""}.
        </p>
      )}
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
