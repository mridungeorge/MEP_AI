"use client";
import { useEffect, useState } from "react";
import { EmptyState } from "@/components/EmptyState";
import { NewProjectForm, STATES } from "@/components/NewProjectForm";
import { OnboardingWizard } from "@/components/OnboardingWizard";
import { ApiError, api } from "@/lib/api";
import type { ProjectRow } from "@/lib/types";

const LABEL: Record<string, string> = { drafting: "Drafting", in_review: "In review", signed: "Signed" };

/** All the firm's projects with where each stands, filterable by state, edition and status. */
export function ProjectsScreen({ demoEnabled = false }: { demoEnabled?: boolean }) {
  const [rows, setRows] = useState<ProjectRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState("");
  const [edition, setEdition] = useState("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  useEffect(() => {
    api.projects({ state, edition, status, q }).then(setRows).catch((e: unknown) => setError(e instanceof ApiError ? e.message : String(e)));
  }, [state, edition, status, q]);
  const goGate1 = (r: { project_id: string; revision_id: string }) => { window.location.assign(`/projects/${r.project_id}/revisions/${r.revision_id}/gate1`); };
  const filtered = state !== "" || edition !== "" || status !== "" || q.trim() !== "";
  const firmEmpty = rows !== null && rows.length === 0 && !filtered && !error;
  return (
    <main>
      <h1>Projects</h1>
      {firmEmpty && <OnboardingWizard projects={rows} onCreated={goGate1} demoEnabled={demoEnabled} />}
      {!firmEmpty && <NewProjectForm onCreated={goGate1} />}
      <p>
        <label>State <select aria-label="State" value={state} onChange={(e) => setState(e.target.value)}><option value="">all</option>{STATES.map((s) => <option key={s}>{s}</option>)}</select></label>{" "}
        <label>NCC edition <select aria-label="NCC edition" value={edition} onChange={(e) => setEdition(e.target.value)}><option value="">all</option><option>NCC2022</option><option>NCC2025</option></select></label>{" "}
        <label>Status <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">all</option>{Object.entries(LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>{" "}
        <label>Search <input aria-label="Search address" value={q} onChange={(e) => setQ(e.target.value)} /></label>
      </p>
      {error && <p role="alert">{error}</p>}
      {rows && rows.length === 0 && filtered && <EmptyState title="No projects match." next="Clear a filter or the search box to see more." />}
      {rows && rows.length > 0 && (
        <table data-testid="projects">
          <thead><tr><th>Project</th><th>State</th><th>NCC</th><th>Status</th><th>Latest revision</th><th>Gates signed</th><th /></tr></thead>
          <tbody>{rows.map((p) => (
            <tr key={p.id} data-testid="project-row">
              <td><a href={`/projects/${p.id}`}>{p.address}</a></td><td>{p.state}</td><td>{p.ncc_edition}</td><td>{LABEL[p.status]}</td>
              <td>{p.latest_revision ? `Rev ${p.latest_revision.architect_rev}${p.latest_revision.frozen ? " (frozen)" : ""}` : "none"} of {p.revisions}</td>
              <td>{p.latest_revision?.gates_signed.join(", ") || "none"}</td>
              <td>{p.latest_revision && <a href={`/projects/${p.id}/revisions/${p.latest_revision.id}/gate1`}>Open</a>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
    </main>
  );
}
