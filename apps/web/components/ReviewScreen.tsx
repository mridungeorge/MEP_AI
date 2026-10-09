"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Me, Package, ReviewLine, ShareLinkView, Worksheet } from "@/lib/types";
import { PackageView } from "./PackageView";

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

const CLASS_LABEL: Record<string, string> = {
  fail: "FAIL", needs_judgement: "needs judgement", stale: "stale", flipped: "outcome changed", near_miss: "near miss",
  not_applicable: "not applicable", lookup_input: "address-lookup input", clean_pass: "clean pass",
};

function Line({ line, canReview, sampleId, onDecide }: {
  line: ReviewLine; canReview: boolean; sampleId: string | undefined;
  onDecide: (id: string, decision: string, reason: string, sample?: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const done = line.decision !== null;
  return (
    <tr data-testid="review-line" data-class={line.review_class ?? ""} data-sample={line.in_sample} data-decision={line.decision ?? ""}>
      <td>{line.subject_id}{line.part !== null ? ` (part ${line.part + 1})` : ""}</td>
      <td>{line.rule_id}<br /><small>{line.citation.document} {line.citation.clause} ({line.citation.rule_status})</small></td>
      <td>{line.outcome}</td>
      <td>{CLASS_LABEL[line.review_class ?? ""] ?? "unclassified"}{line.reasons.length > 0 && <small><br />{line.reasons.join("; ")}</small>}
        {line.in_sample && !done && <strong><br />spot-check: examine this one</strong>}</td>
      <td>
        {done ? <span>{line.decision}{line.bulk ? " (bulk)" : ""}: {line.reason}</span> : canReview ? (
          <>
            <input aria-label={`Reason for ${line.rule_id} ${line.subject_id}`} value={reason} maxLength={2000}
                   placeholder="reason (required)" onChange={(e) => setReason(e.target.value)} />{" "}
            {(["approve", "reject", "request_changes"] as const).map((d) => (
              <button key={d} type="button" disabled={reason.trim().length < 3}
                      onClick={() => void onDecide(line.id, d, reason.trim(), line.in_sample ? sampleId : undefined)}>
                {d === "request_changes" ? "Request changes" : d === "approve" ? "Approve" : "Reject"}
              </button>
            ))}
          </>
        ) : <em>not reviewed</em>}
      </td>
    </tr>
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

      <table data-testid="review-table">
        <thead><tr><th>System</th><th>Rule / clause</th><th>Outcome</th><th>Class</th><th>Decision</th></tr></thead>
        <tbody>
          {ws.results.map((l) => (
            <Line key={l.id} line={l} canReview={isChecker && reviewOpen} sampleId={sampleId}
                  onDecide={(id, d, r, s) => act(async () => { await api.decide(revisionId, id, d, r, s); })} />
          ))}
        </tbody>
      </table>

      <section aria-label="Sign-off">
        <h2>Sign-off</h2>
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
            setNewLink(`${window.location.origin}/share/${r.token}`);
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
