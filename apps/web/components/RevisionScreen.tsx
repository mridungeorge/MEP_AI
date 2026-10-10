"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { Lineage, RevisionDiff, RevisionResults, SpaceDiffItem } from "@/lib/types";
import { FixPanel } from "./FixPanel";
import { NewRevisionPanel } from "./NewRevisionPanel";

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

const show = (v: unknown) => (v === null || v === undefined ? "none" : String(v));

function SpaceChange({ item }: { item: SpaceDiffItem }) {
  return (
    <tr data-testid={`space-change-${item.change}`}>
      <td>{item.change}</td>
      <td>{item.name ?? item.key}</td>
      <td>
        {item.change === "changed"
          ? item.fields.map((f) => `${f.field}: ${show(f.old)} -> ${show(f.new)}`).join("; ")
          : item.change === "added" ? `area ${show(item.new?.area_m2)} m2` : `was area ${show(item.old?.area_m2)} m2`}
      </td>
      <td>{item.matched_by ?? ""}</td>
      <td>{item.change === "removed" ? "" : item.confirmed ? "confirmed" : "needs Gate 1 confirmation"}</td>
    </tr>
  );
}

/** A revision with its lineage: the diff from its parent, the results the diff makes stale, WHY (the reasoning trace, built from
 *  dependency-graph edges and stored results only), cross-rule conflicts, and the designer's confirmation of the diff. */
export function RevisionScreen({ projectId, revisionId }: { projectId: string; revisionId: string }) {
  const [diff, setDiff] = useState<RevisionDiff | null>(null);
  const [lineage, setLineage] = useState<Lineage | null>(null);
  const [results, setResults] = useState<RevisionResults | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [d, l, r] = await Promise.all([api.diff(revisionId), api.lineage(revisionId), api.results(revisionId)]);
      setDiff(d); setLineage(l); setResults(r);
    } catch (e) {
      setMessage(refusal(e));
    }
  }, [revisionId]);
  useEffect(() => { void reload(); }, [reload]);

  if (!diff || !lineage || !results) return <p>{message ?? "Loading revision..."}</p>;
  const act = async (fn: () => Promise<string>) => {
    setMessage(null);
    try { setMessage(await fn()); } catch (e) { setMessage(refusal(e)); }
    await reload();
  };
  const gate1 = `/projects/${projectId}/revisions/${revisionId}/gate1`;
  const pending = diff.needs_confirmation.length;
  const frozen = lineage.revision.frozen;

  return (
    <main>
      <h1>Revision {lineage.revision.architect_rev}{frozen ? " (frozen)" : ""}</h1>
      <nav aria-label="Lineage">
        {lineage.ancestors.length > 0 && (
          <span>Derived from {[...lineage.ancestors].reverse().map((a, i) => (
            <span key={a.id}>{i > 0 && " > "}<a href={`/projects/${projectId}/revisions/${a.id}/diff`}>Rev {a.architect_rev}</a></span>
          ))}. </span>
        )}
        {lineage.children.length > 0 && (
          <span>Later revisions: {lineage.children.map((c) => (
            <a key={c.id} href={`/projects/${projectId}/revisions/${c.id}/diff`}>Rev {c.architect_rev} </a>
          ))}</span>
        )}
        <a href={gate1}> Gate 1 for this revision</a>
      </nav>

      {diff.parent && (
        <section aria-label="Changes from the previous revision">
          <h2>Changes from Rev {diff.parent.architect_rev}</h2>
          {!diff.has_changes && <p>No differences from the previous revision.</p>}
          {diff.spaces.length > 0 && (
            <table>
              <thead><tr><th>Change</th><th>Space</th><th>Detail</th><th>Matched by</th><th>Gate 1</th></tr></thead>
              <tbody>{diff.spaces.map((s) => <SpaceChange key={`${s.change}-${s.key}`} item={s} />)}</tbody>
            </table>
          )}
          {diff.inputs.length > 0 && (
            <ul data-testid="input-changes">
              {diff.inputs.map((i) => (
                <li key={`${i.system}-${i.name}`}>{i.system} / {i.name}: {i.change} ({show(i.old)} -&gt; {show(i.new)})</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {diff.conflicts && diff.conflicts.length > 0 && (
        <section aria-label="Rule conflicts" data-testid="conflicts">
          <h2>Rule conflicts</h2>
          <ul>{diff.conflicts.map((c) => <li key={`${c.subject_id}-${c.input}-${c.better_rule}`}>{c.text}</li>)}</ul>
        </section>
      )}

      {diff.traces.length > 0 && (
        <section aria-label="Reasoning trace" data-testid="trace">
          <h2>Why these results are stale</h2>
          <p>Each line follows the rule dependency graph from the change to the result; nothing is guessed.</p>
          {diff.traces.map((t) => (
            <details key={`${t.subject_id}-${t.rule_id}`} open>
              <summary>{t.subject_id} / {t.rule_id}{t.before ? ` (was ${t.before}${t.after ? `, now ${t.after}` : ""})` : ""}</summary>
              <ol>{t.lines.map((l, i) => <li key={i}>{l}</li>)}</ol>
            </details>
          ))}
        </section>
      )}

      <section aria-label="Results">
        <h2>Results {results.source === "carried_from_parent" ? "(carried from the previous revision; not re-run yet)" : ""}</h2>
        {results.results.length === 0 ? <p>No results yet.</p> : (
          <table>
            <thead><tr><th>Subject</th><th>Rule</th><th>Outcome</th><th></th><th></th></tr></thead>
            <tbody>{results.results.map((r) => (
              <tr key={`${r.subject_id}-${r.rule_id}-${r.part ?? ""}`} data-stale={r.stale}>
                <td>{r.subject_id}</td><td>{r.rule_id}</td><td>{r.outcome}</td><td>{r.stale ? "STALE: re-run needed" : ""}</td>
                <td>{r.outcome === "FAIL" && !frozen && <FixPanel revisionId={revisionId} subject={r.subject_id} rule={r.rule_id} onApplied={() => window.location.reload()} />}</td>
              </tr>))}</tbody>
          </table>
        )}
      </section>

      {!frozen && (
        <section aria-label="Actions">
          {diff.parent && (
            <>
              {pending > 0 && <p data-testid="diff-pending">{pending} changed or added space(s) must be confirmed at Gate 1 first. <a href={gate1}>Open Gate 1</a></p>}
              <button type="button" disabled={diff.confirmed || pending > 0}
                      onClick={() => act(async () => { await api.confirmDiff(revisionId, diff.hash ?? ""); return "Diff confirmed."; })}>
                {diff.confirmed ? "Diff confirmed" : "Confirm this diff"}
              </button>{" "}
            </>
          )}
          <button type="button" disabled={Boolean(diff.parent && !diff.confirmed)}
                  onClick={() => act(async () => { const r = await api.runRules(revisionId); return `Run complete: ${r.run_id}`; })}>
            Re-run rules
          </button>{" "}
          <button type="button" disabled={results.source !== "own"}
                  onClick={() => act(async () => { await api.freeze(revisionId); return "Revision frozen."; })}>
            Freeze revision
          </button>
          {message && <p role="alert">{message}</p>}
        </section>
      )}

      {frozen && <NewRevisionPanel projectId={projectId} revisionId={revisionId} />}
    </main>
  );
}
