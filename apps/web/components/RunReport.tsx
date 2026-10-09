import type { Report } from "@/lib/types";

/** Shows the engine's cited report as returned. Outcomes, clauses and the draft banner are the API's, never computed here. */
export function RunReport({ report }: { report: Report }) {
  return (
    <section aria-label="Report">
      <h2>Report</h2>
      {report.banner && <p role="status" data-testid="draft-banner" style={{ background: "#fef3c7", padding: 8 }}>{report.banner}</p>}
      <table>
        <thead>
          <tr><th>System</th><th>Rule</th><th>Outcome</th><th>Citation</th></tr>
        </thead>
        <tbody>
          {report.results.map((r, i) => (
            <tr key={i} data-testid="report-row">
              <td>{r.subject_id}</td>
              <td>{r.rule_id}</td>
              <td>{r.outcome}</td>
              <td>{r.citation.document} {r.citation.edition}, {r.citation.clause} ({r.citation.rule_status})</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
