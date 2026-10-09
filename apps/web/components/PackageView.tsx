import type { Package } from "@/lib/types";

/** Read-only view of the signed compliance package (the signed-in screen and the certifier's share page both use it). */
export function PackageView({ pkg }: { pkg: Package }) {
  const st = pkg.status;
  return (
    <section aria-label="Compliance package">
      {pkg.banner && <p role="status" data-testid="draft-banner" style={{ background: "#fef3c7", padding: 8 }}>{pkg.banner}</p>}
      {pkg.independence_notice && (
        <p role="status" data-testid="independence-notice" style={{ background: "#fee2e2", color: "#7f1d1d", padding: 8, fontWeight: 700 }}>
          {pkg.independence_notice}: one person may have held more than one gate in this firm.
        </p>
      )}
      <p data-testid="package-status" style={{ fontWeight: 600, color: st.complete && pkg.ledger.verified ? "#166534" : "#b91c1c" }}>
        {st.complete && !pkg.ledger.verified ? "NOT VERIFIED: the audit ledger hash chain does not verify" : st.complete ? "SIGNED: Gate 1 (designer), Gate 2 (checker) and Gate 3 (approver)"
          : `NOT FULLY SIGNED: missing ${st.missing.map((g) => g.replace("gate", "Gate ")).join(", ")}`}
      </p>
      <p>
        {pkg.project.address} | {pkg.project.ncc_edition} | {pkg.project.state} | climate zone {pkg.project.climate_zone} | architect
        revision {pkg.revision.architect_rev}
        {pkg.revision.derived_from.length > 0 ? ` (derived from ${[...pkg.revision.derived_from].reverse().join(" > ")})` : ""}
      </p>
      {pkg.accepted_fails.length > 0 && (
        <>
          <h3>Accepted FAILs (an engineer decided to accept a failed rule)</h3>
          <table data-testid="accepted-fails">
            <thead><tr><th>Subject</th><th>Rule</th><th>Category</th><th>Reference</th><th>Explanation</th><th>Approver acknowledgement</th></tr></thead>
            <tbody>
              {pkg.accepted_fails.map((r) => (
                <tr key={`${r.subject}-${r.rule_id}`}>
                  <td>{r.subject}</td><td>{r.rule_id}<br /><small>{r.citation.document} {r.citation.clause}</small></td>
                  <td>{r.accepted_fail.category}</td><td>{r.accepted_fail.reference ?? "-"}</td><td>{r.accepted_fail.explanation}</td>
                  <td>{r.accepted_fail.acknowledged ? `${r.accepted_fail.acknowledgement} (${r.accepted_fail.acknowledged_by ?? ""})` : "NOT ACKNOWLEDGED"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <h3>Sign-offs</h3>
      <table data-testid="signoffs">
        <thead><tr><th>Gate</th><th>Role</th><th>Signer</th><th>Registration no.</th><th>Signed at</th></tr></thead>
        <tbody>
          {pkg.signoffs.map((s) => (
            <tr key={s.gate}><td>{s.gate.replace("gate", "Gate ")}</td><td>{s.role}</td><td>{s.email ?? "-"}</td>
              <td>{s.registration_no ?? "-"}</td><td>{s.signed_at}</td></tr>
          ))}
        </tbody>
      </table>
      <h3>Results and review</h3>
      <table data-testid="package-results">
        <thead><tr><th>Subject</th><th>Rule</th><th>Clause</th><th>Outcome</th><th>Class</th><th>Review</th></tr></thead>
        <tbody>
          {pkg.results.map((r) => (
            <tr key={`${r.subject}-${r.rule_id}-${r.part ?? ""}`}>
              <td>{r.subject}</td><td>{r.rule_id}</td><td>{r.citation.document} {r.citation.clause} ({r.citation.rule_status})</td>
              <td>{r.outcome}</td><td>{r.review_class ?? "-"}</td>
              <td>{r.decision ? `${r.accepted_fail ? "accepted FAIL" : r.decision}${r.bulk ? " (bulk, after spot-check)" : ""}: ${r.reason}` : "not reviewed"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p data-testid="ledger-status">
        Audit ledger hash chain {pkg.ledger.verified ? "verified" : `NOT verified: ${pkg.ledger.reason}`} when this package was made;
        anchored at the last sign-off (event {pkg.ledger.anchor_seq}, {pkg.ledger.anchor_hash?.slice(0, 16)}).
      </p>
    </section>
  );
}
