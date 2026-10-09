"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { RevisionSummary } from "@/lib/types";

/** The signed-in user's revisions (the API lists only their firm's), each linking to its Gate 1 screen. */
export default function Home() {
  const [revisions, setRevisions] = useState<RevisionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.revisions().then(setRevisions).catch((e: unknown) =>
      setError(e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e)));
  }, []);
  if (error) return <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>;
  if (!revisions) return <p>Loading revisions…</p>;
  return (
    <main>
      <h1>Revisions</h1>
      {revisions.length === 0 && <p>No revisions yet.</p>}
      <table>
        <thead>
          <tr><th>Project</th><th>State</th><th>NCC</th><th>Architect rev</th><th>Status</th><th /></tr>
        </thead>
        <tbody>
          {revisions.map((r) => (
            <tr key={r.id} data-testid="revision-row">
              <td>{r.address}</td><td>{r.state}</td><td>{r.ncc_edition}</td><td>{r.architect_rev}</td>
              <td>{r.frozen ? "frozen" : r.status}</td>
              <td><a href={`/projects/${r.project_id}/revisions/${r.id}/gate1`}>Open Gate 1</a></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
