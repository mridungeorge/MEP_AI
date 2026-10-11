"use client";
import { HelpTip } from "@/components/HelpTip";
import { EmptyState } from "@/components/EmptyState";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Me, Package, ReviewLine, ShareLinkView, Worksheet } from "@/lib/types";
import { AgentPanel } from "./AgentPanel";
import { CommissioningPanel } from "./CommissioningPanel";
import { DeclarationPanel } from "./DeclarationPanel";
import { PackageView } from "./PackageView";

const REVIEW_AGENTS = [
  { id: "adversarial_checker", label: "Ask the adversarial checker to look for problems", prompt: "Look for reasons these results or their inputs could be wrong." },
  { id: "compliance_risk", label: "Ask the compliance-risk agent for risks", prompt: "Assess the delivery and compliance risks of this revision." },
] as const;
const REVIEW_KINDS = ["flag", "risk"];

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

const CLASS_LABEL: Record<string, string> = {
  fail: "FAIL", needs_judgement: "needs judgement", stale: "stale", flipped: "outcome changed", near_miss: "near miss",
  not_applicable: "not applicable", lookup_input: "address-lookup input", clean_pass: "clean pass",
};

const FAIL_CATEGORIES = [
  ["performance_solution", "Performance solution (give the reference)"],
  ["rule_disputed", "Rule disputed (goes to the engineer review list)"],
  ["out_of_scope", "Out of scope of this engagement"],
] as const;

function Line({ line, canReview, sampleId, onDecide }: {
  line: ReviewLine; canReview: boolean; sampleId: string | undefined;
  onDecide: (id: string, decision: string, reason: string, sample?: string, category?: string, reference?: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const [category, setCategory] = useState("");
  const [reference, setReference] = useState("");
  const isFail = line.outcome === "FAIL";
  const failOk = category !== "" && reason.trim().length >= 10 && (category !== "performance_solution" || reference.trim().length >= 3);
  const done = line.decision !== null;
  return (
    <tr data-testid="review-line" data-class={line.review_class ?? ""} data-sample={line.in_sample} data-decision={line.decision ?? ""}>
      <td>{line.subject_id}{line.part !== null ? ` (part ${line.part + 1})` : ""}</td>
      <td>{line.rule_id}<br /><small>{line.citation.document} {line.citation.clause} ({line.citation.rule_status})</small></td>
      <td>{line.outcome}</td>
      <td>{CLASS_LABEL[line.review_class ?? ""] ?? "unclassified"}{line.reasons.length > 0 && <small><br />{line.reasons.join("; ")}</small>}
        {line.in_sample && !done && <strong><br />spot-check: examine this one</strong>}</td>
      <td>
        {done ? <span>{line.fail_category ? `accepted FAIL (${line.fail_category}${line.fail_reference ? `: ${line.fail_reference}` : ""})` : line.decision}{line.bulk ? " (bulk)" : ""}: {line.reason}{line.fail_category && (line.acknowledged ? " [approver acknowledged]" : " [awaiting approver acknowledgement]")}</span> : canReview ? (
          <>
            <input aria-label={`Reason for ${line.rule_id} ${line.subject_id}`} value={reason} maxLength={2000}
                   placeholder="reason (required)" onChange={(e) => setReason(e.target.value)} />{" "}
            {isFail && (
              <>
                <select aria-label={`Accepted FAIL category for ${line.rule_id}`} value={category} onChange={(e) => setCategory(e.target.value)}>
                  <option value="">accept this FAIL because…</option>
                  {FAIL_CATEGORIES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
                </select>{" "}
                {category === "performance_solution" && (
                  <input aria-label={`Performance solution reference for ${line.rule_id}`} value={reference} maxLength={500}
                         placeholder="reference" onChange={(e) => setReference(e.target.value)} />
                )}{" "}
              </>
            )}
            {(["approve", "reject", "request_changes"] as const).map((d) => (
              <button key={d} type="button" disabled={reason.trim().length < 3 || (isFail && d === "approve" && !failOk)}
                      onClick={() => void onDecide(line.id, d, reason.trim(), line.in_sample ? sampleId : undefined,
                                                   isFail && d === "approve" ? category : undefined, reference.trim())}>
                {d === "request_changes" ? "Request changes" : d === "approve" ? (isFail ? "Accept this FAIL" : "Approve") : "Reject"}
              </button>
            ))}
          </>
        ) : <em>not reviewed</em>}
      </td>
    </tr>
  );
}

function AckRow({ line, onAck }: { line: ReviewLine; onAck: (note: string) => Promise<void> }) {
  const [note, setNote] = useState("");
  return (
    <p data-testid="ack-row">
      <strong>{line.rule_id}</strong> ({line.subject_id}): {line.fail_category}{line.fail_reference ? ` - ${line.fail_reference}` : ""}: {line.reason}{" "}
      {line.acknowledged ? <em>acknowledged</em> : (
        <>
          <input aria-label={`Acknowledgement for ${line.rule_id}`} value={note} maxLength={2000} placeholder="acknowledgement (required)"
                 onChange={(e) => setNote(e.target.value)} />{" "}
          <button type="button" disabled={note.trim().length < 3} onClick={() => void onAck(note.trim())}>Acknowledge</button>
        </>
      )}
    </p>
  );
}

/** Gate 2 (checker) and Gate 3 (approver): line-by-line review with reasons, bulk approval of clean passes after a random spot-check,
 *  the sign-off buttons, the signed package and the certifier share links. Every rule of the process is the server's; this only
 *  presents it and shows its refusals verbatim. */
export function ReviewScreen({ revisionId }: { revisionId: string }) {
  const [ws, setWs] = useState<Worksheet | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [pkg, setPkg] = useState<Package | null>(null);
  const [links, setLinks] = useState<ShareLinkView[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [registration, setRegistration] = useState("");
  const [newLink, setNewLink] = useState<string | null>(null);
  const [days, setDays] = useState(7);
  const [label, setLabel] = useState("");

  const reload = useCallback(async () => {
    try {
      const [w, m] = await Promise.all([api.worksheet(revisionId), api.me()]);
      setWs(w); setMe(m);
      setPkg(await api.pkg(revisionId));
      setLinks(w.signoffs.some((s) => s.gate === "gate3") ? await api.shareLinks(revisionId) : []);
    } catch (e) {
      setMessage(refusal(e));
    }
  }, [revisionId]);
  useEffect(() => { void reload(); }, [reload]);

  if (!ws || !me) return <p>{message ?? "Loading the review…"}</p>;
  const act = async (fn: () => Promise<string | void>) => {
    setMessage(null);
    try { const m = await fn(); if (m) setMessage(m); } catch (e) { setMessage(refusal(e)); }
    await reload();
  };
  const signed = new Set(ws.signoffs.map((s) => s.gate));
  const isChecker = me.role === "checker";
  const isApprover = me.role === "approver";
  const canShare = (me.role === "designer" || isApprover) && signed.has("gate3");
  const reviewOpen = ws.revision.frozen && !signed.has("gate2");
  const sampleId = ws.sample?.id;

  return (
    <main>
      <h1>Review and sign-off: Rev {ws.revision.architect_rev}</h1>
      <p><HelpTip topic="review" label="What does each decision do?" /></p>
      {me.independence_notice && (
        <p role="status" data-testid="independence-notice-live" style={{ background: "#fee2e2", color: "#7f1d1d", padding: 8, fontWeight: 700 }}>
          {me.independence_notice}: this firm lets one person hold more than one gate.
        </p>
      )}
      <p data-testid="review-progress">
        {ws.approved} of {ws.total} results approved. Signed: {[...signed].map((g) => g.replace("gate", "Gate ")).join(", ") || "none"}.
        {" "}Classes: {Object.entries(ws.by_class).map(([k, n]) => `${CLASS_LABEL[k] ?? k} ${n}`).join(", ")}.
      </p>
      {!ws.revision.frozen && <p role="status">This revision is not frozen yet: the designer signs Gate 1 by freezing it.</p>}

      {isChecker && reviewOpen && (
        <section aria-label="Bulk approval of clean passes">
          <h2>Clean passes ({ws.open_clean} not yet reviewed)</h2>
          <p>Clean passes may be approved together, but only after you examine a random sample of them yourself. Any problem in the
            sample means every clean pass must be reviewed line by line.</p>
          {!ws.sample ? (
            <button type="button" disabled={ws.open_clean === 0}
                    onClick={() => act(async () => { await api.prepareBulk(revisionId); })}>Draw the spot-check sample</button>
          ) : (
            <>
              <p data-testid="sample-info">Spot-check sample: {ws.sample.size} of {ws.sample.of}. Review the highlighted lines below, then:</p>
              <button type="button" onClick={() => act(async () => {
                const r = await api.bulkApprove(revisionId, ws.sample!.id);
                return `${r.approved} clean pass(es) approved in bulk.`;
              })}>Approve the remaining clean passes</button>
            </>
          )}
        </section>
      )}

      <AgentPanel revisionId={revisionId} title="Flags and risks raised by agents" agents={[...REVIEW_AGENTS]} kinds={REVIEW_KINDS} />

      {ws.results.length === 0 && <EmptyState title="No results to review." next="Run the rules at Gate 1 first; results appear here once a run exists." />}
      <table data-testid="review-table">
        <thead><tr><th>System</th><th>Rule / clause</th><th>Outcome</th><th>Class</th><th>Decision</th></tr></thead>
        <tbody>
          {ws.results.map((l) => (
            <Line key={l.id} line={l} canReview={isChecker && reviewOpen} sampleId={sampleId}
                  onDecide={(id, d, r, s, c, ref) => act(async () => { await api.decide(revisionId, id, d, r, s, c, ref); })} />
          ))}
        </tbody>
      </table>

      {isApprover && signed.has("gate2") && !signed.has("gate3") && ws.results.some((l) => l.fail_category) && (
        <section aria-label="Accepted FAILs to acknowledge" data-testid="ack-section">
          <h2>Accepted FAILs: acknowledge each one before Gate 3</h2>
          {ws.results.filter((l) => l.fail_category).map((l) => <AckRow key={l.id} line={l}
            onAck={(note) => act(async () => { await api.acknowledgeFail(revisionId, l.id, note); })} />)}
        </section>
      )}
      <section aria-label="Sign-off">
        <h2>Sign-off</h2>
        <p><HelpTip topic="signoff" label="What is the registration number for?" /></p>
        {isChecker && !signed.has("gate2") && (
          <button type="button" onClick={() => act(async () => { await api.sign(revisionId, "gate2"); return "Gate 2 signed."; })}>
            Sign Gate 2 (checker)
          </button>
        )}
        {isApprover && signed.has("gate2") && !signed.has("gate3") && (
          <>
            <label>Registration number <input value={registration} maxLength={40} onChange={(e) => setRegistration(e.target.value)} /></label>{" "}
            <button type="button" disabled={registration.trim().length < 3}
                    onClick={() => act(async () => { await api.sign(revisionId, "gate3", registration.trim()); return "Gate 3 signed."; })}>
              Sign Gate 3 (approver)
            </button>
          </>
        )}
        {!isChecker && !isApprover && <p>Gate 2 is signed by a checker and Gate 3 by an approver.</p>}
        {message && <p role="alert">{message}</p>}
      </section>

      {pkg && <PackageView pkg={pkg} />}
      {pkg && pkg.project.state === "NSW" && <DeclarationPanel revisionId={revisionId} />}
      {pkg && pkg.status.complete && <CommissioningPanel revisionId={revisionId} />}
      <p>
        <button type="button" onClick={() => act(async () => {
          const blob = await api.pdfBlob(revisionId);
          window.open(URL.createObjectURL(blob), "_blank");
        })}>Open the PDF</button>
      </p>

      {canShare && (
        <section aria-label="Certifier share link">
          <h2>Certifier share link</h2>
          <p>A read-only link for the certifier, time-limited, and every opening is logged. It is shown once.</p>
          <label>Days <input type="number" min={1} max={30} value={days} onChange={(e) => setDays(Number(e.target.value))} /></label>{" "}
          <label>Label <input value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} /></label>{" "}
          <button type="button" onClick={() => act(async () => {
            const r = await api.createShare(revisionId, days, label);
            setNewLink(`${window.location.origin}/share#${r.token}`);
          })}>Create share link</button>
          {newLink && <p>Copy it now: <code data-testid="share-url">{newLink}</code></p>}
          <ul data-testid="share-links">
            {links.map((l) => (
              <li key={l.id}>{l.label ?? "link"} {l.id}: expires {l.expires_at}, opened {l.views} time(s){l.revoked ? ", REVOKED" : ""}{" "}
                {!l.revoked && <button type="button" onClick={() => act(async () => { await api.revokeShare(revisionId, l.id); })}>Revoke</button>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
