"use client";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { FixesResponse } from "@/lib/types";

/** Fix hypotheses for one failed result. Every option is the smallest single change the rule engine finds that makes THIS rule pass; it is offered for applying only
 *  if every other rule that reads the same input still passes. Applying makes an unconfirmed change that goes through Gate 1 and the gates again. */
export function FixPanel({ revisionId, subject, rule, onApplied }: { revisionId: string; subject: string; rule: string; onApplied: () => void }) {
  const [data, setData] = useState<FixesResponse | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fail = (e: unknown) => setMessage(e instanceof ApiError ? `${e.code ?? e.status}: ${e.message}` : String(e));
  return (
    <details onToggle={(e) => { if ((e.target as HTMLDetailsElement).open && !data) void api.fixes(revisionId, subject, rule).then(setData).catch(fail); }}>
      <summary>Fix hypotheses</summary>
      {!data && !message && <p>Looking for options…</p>}
      {data && (
        <div data-testid="fix-options">
          <p><strong>{data.label}</strong>: {data.note}</p>
          {data.options.length === 0 && <p>No single change to an input the engineer decides makes this rule pass. See the rule&apos;s own suggestions below, or the Performance Solution pathway.</p>}
          <ul>{data.options.map((o) => (
            <li key={o.id} data-accepted={o.accepted}>
              {o.label}{" "}
              {o.accepted ? <span>Breaks no dependent rule ({o.dependent_rules.length}){o.needs_judgement && o.needs_judgement.length > 0 ? `; ${o.needs_judgement.length} would then need a person's judgement: ${o.needs_judgement.join(", ")}` : ""}.</span> : <strong>Withdrawn: {o.conflicts.length > 0 ? o.conflicts.join("; ") : "it does not satisfy every dependent rule."}</strong>}{" "}
              <button type="button" disabled={!o.accepted} onClick={() => {
                setMessage(null);
                void api.makeFixScratch(revisionId, subject, rule, o.id).then((s) => api.applyFixScratch(revisionId, s.scratch_id)).then((r) => { setMessage(r.needs); onApplied(); }).catch(fail);
              }}>Apply as an unconfirmed change</button>
            </li>
          ))}</ul>
          {data.rule_text_hypotheses.length > 0 && <ul aria-label="The rule's own suggestions (not tested)">{data.rule_text_hypotheses.map((t) => <li key={t}>{t}</li>)}</ul>}
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </details>
  );
}
