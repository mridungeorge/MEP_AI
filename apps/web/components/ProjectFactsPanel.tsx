import type { ProjectFacts } from "@/lib/types";

/**
 * The facts that choose the NCC edition and the rule pack. They are shown as stored; a designer confirms them like any
 * other Gate 1 value (the server records who and when, and writes the ledger).
 */
export function ProjectFactsPanel({
  project, selected, onToggle,
}: { project: ProjectFacts; selected: boolean; onToggle: () => void }) {
  return (
    <section aria-label="Project facts">
      <h2>Project facts</h2>
      <table>
        <thead>
          <tr><th>Select</th><th>State</th><th>NCC edition</th><th>Climate zone</th><th>Approval date</th><th>Status</th></tr>
        </thead>
        <tbody>
          <tr data-state={project.confirmed ? "confirmed" : "unconfirmed"} data-testid="project-facts-row">
            <td>
              <input type="checkbox" checked={selected} onChange={onToggle} aria-label="Select project facts" />
            </td>
            <td>{project.state}</td>
            <td>{project.ncc_edition}</td>
            <td>{project.climate_zone ?? <em>not set</em>}</td>
            <td>{project.approval_date ?? <em>not set</em>}</td>
            <td>{project.confirmed ? "confirmed" : "not confirmed"}</td>
          </tr>
        </tbody>
      </table>
    </section>
  );
}
