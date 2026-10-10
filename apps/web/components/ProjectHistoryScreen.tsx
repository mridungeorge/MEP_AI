"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { ProjectHistory } from "@/lib/types";

/** A project's revisions in order, with the results and who signed which gate. */
export function ProjectHistoryScreen({ projectId }: { projectId: string }) {
  const [h, setH] = useState<ProjectHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.projectHistory(projectId).then(setH).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e))); }, [projectId]);
  if (error) return <p role="alert">{error}</p>;
  if (!h) return <p>Loading…</p>;
  return (
    <main>
      <h1>{h.project.address}</h1>
      <p>{h.project.state}, {h.project.ncc_edition}{h.project.climate_zone ? `, climate zone ${h.project.climate_zone}` : ""}. <a href="/projects">All projects</a></p>
      <ol data-testid="history">{h.revisions.map((r) => (
        <li key={r.id}>
          <strong>Rev {r.architect_rev}</strong> ({r.stage.replace("_", " ")}), created {r.created_at.slice(0, 10)}{r.frozen_at ? `, frozen ${r.frozen_at.slice(0, 10)}` : ""}.{" "}
          {Object.keys(r.results).length > 0 && <span>Results: {Object.entries(r.results).map(([k, v]) => `${v} ${k}`).join(", ")}. </span>}
          <a href={`/projects/${h.project.id}/revisions/${r.id}/review`}>Review</a>
          <ul>{r.signoffs.map((s) => (
            <li key={s.gate}>{s.gate}: {s.signer ?? "unknown"} on {s.signed_at.slice(0, 10)}{s.registration_no ? `, registration ${s.registration_no}` : ""}{s.signer_mode === "small_firm" ? " (NOT INDEPENDENTLY CHECKED)" : ""}</li>
          ))}</ul>
        </li>
      ))}</ol>
    </main>
  );
}
