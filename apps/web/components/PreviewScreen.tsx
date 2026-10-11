"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { loadIfcParts } from "@/lib/ifcload";
import { type Buffers, MAX_TRIANGLES, TooLarge, mergeParts, partsCentre, triangleCount } from "@/lib/ifcmesh";
import type { BaseModelRow, SkillRunRow } from "@/lib/types";
import { IfcViewer } from "./IfcViewer";

interface Source { key: string; label: string; kind: "architect" | "services"; load: () => Promise<ArrayBuffer> }

/** 3D preview of the architect's model and the IFC files the drafting skills released. The architect's model is drawn see-through so the services show inside it. */
export function PreviewScreen({ revisionId }: { revisionId: string }) {
  const [sources, setSources] = useState<Source[]>([]);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [layers, setLayers] = useState<Buffers[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [models, runs] = await Promise.all([api.baseModels(revisionId), api.skillRuns(revisionId)]);
      const out: Source[] = models.models.map((m: BaseModelRow) => ({ key: `m:${m.id}`, label: `Architect model: ${m.file_name}`, kind: "architect" as const, load: () => api.baseModelBytes(m.id) }));
      for (const r of runs as SkillRunRow[]) {
        for (const a of r.artifacts) {
          if (a.released && a.name.toLowerCase().endsWith(".ifc")) out.push({ key: `a:${a.artifact_id}`, label: `${r.skill}: ${a.name}`, kind: "services", load: async () => (await api.artifactBlob(a.artifact_id)).arrayBuffer() });
        }
      }
      setSources(out);
    } catch (e) { setMessage(e instanceof ApiError ? e.message : String(e)); }
  }, [revisionId]);
  useEffect(() => { void refresh(); }, [refresh]);

  const show = async () => {
    setBusy(true); setMessage(null); setLayers(null);
    try {
      const loaded: { kind: Source["kind"]; parts: Awaited<ReturnType<typeof loadIfcParts>> }[] = [];
      let budget = MAX_TRIANGLES;
      for (const s of sources.filter((x) => chosen.has(x.key))) {
        const parts = await loadIfcParts(await s.load(), budget);
        budget -= triangleCount(parts);
        loaded.push({ kind: s.kind, parts });
      }
      const origin = partsCentre((loaded.find((l) => l.kind === "architect") ?? loaded[0])?.parts ?? []);   // ONE origin for every model, so they line up
      setLayers(loaded.map((l) => mergeParts(l.parts, l.kind === "architect" ? 0.3 : 1, origin)));
    } catch (e) {
      setMessage(e instanceof TooLarge ? e.message : e instanceof ApiError ? e.message : `The 3D preview could not read that file (${e instanceof Error ? e.message : "error"}).`);
    } finally { setBusy(false); }
  };

  return (
    <main>
      <h1>3D preview</h1>
      <p>Pick the architect&apos;s model and/or the IFC files released by the drafting skills. Large models are refused rather than left to freeze the page.</p>
      <p>
        <label>Upload the architect&apos;s IFC{" "}
          <input type="file" accept=".ifc" aria-label="Architect IFC" onChange={(e) => { const f = e.target.files?.[0]; if (f) void api.uploadBaseModel(revisionId, f).then(refresh).catch((err) => setMessage(err instanceof ApiError ? err.message : String(err))); }} />
        </label>
      </p>
      {message && <p role="alert">{message}</p>}
      {sources.length === 0 ? <p>Nothing to show yet: upload the architect&apos;s model, or build an IFC with a drafting skill.</p> : (
        <ul>{sources.map((s) => (
          <li key={s.key}><label><input type="checkbox" checked={chosen.has(s.key)}
            onChange={(e) => { const next = new Set(chosen); if (e.target.checked) next.add(s.key); else next.delete(s.key); setChosen(next); }} /> {s.label}</label></li>))}</ul>
      )}
      <button type="button" disabled={busy || chosen.size === 0} onClick={() => void show()}>{busy ? "Reading…" : "Show"}</button>
      {layers && <IfcViewer layers={layers} label="3D view of the selected models" />}
    </main>
  );
}
