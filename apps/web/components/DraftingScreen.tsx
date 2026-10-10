"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { prune, setPath } from "@/lib/specpath";
import type { AgentNote, CardPreview, ShortcutResult, SkillCard, SkillRunRow, SkillRunSummary, SkillSummary } from "@/lib/types";
import { AgentPanel } from "./AgentPanel";
import { SpecForm } from "./SpecForm";

const DESIGNER_AGENT = [{ id: "designer", label: "Ask the assistant", prompt: "Fill in the card for a plant room 6 x 4 m ..." }] as const;
const DESIGNER_KINDS = ["clarifying_question", "spec_card_draft", "fix_hypothesis", "explanation"];

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

async function saveBlob(id: string, name: string) {
  const blob = await api.artifactBlob(id);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Drafting: choose a skill, fill its spec card (or type a sentence and check what was understood), confirm, build. A file is offered only
 *  when the skill's own checks AND the server's independent re-check passed; otherwise the screen says which checks failed. */
export function DraftingScreen({ projectId, revisionId }: { projectId: string; revisionId: string }) {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [name, setName] = useState<string>("");
  const [card, setCard] = useState<SkillCard | null>(null);
  const [spec, setSpec] = useState<Record<string, unknown>>({});
  const [missing, setMissing] = useState<Record<string, string>>({});
  const [sentence, setSentence] = useState("");
  const [proposal, setProposal] = useState<ShortcutResult | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<SkillRunSummary | null>(null);
  const [runs, setRuns] = useState<SkillRunRow[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<AgentNote[]>([]);
  const [review, setReview] = useState<{ note: AgentNote; preview: CardPreview } | null>(null);
  const loadDrafts = useCallback(async () => {
    try { setDrafts((await api.agentNotes(revisionId, "spec_card_draft")).filter((n) => n.status === "open" && n.spec)); } catch { /* the panel shows its own errors */ }
  }, [revisionId]);
  useEffect(() => { void loadDrafts(); }, [loadDrafts]);

  const loadRuns = useCallback(async () => { try { setRuns(await api.skillRuns(revisionId)); } catch (e) { setMessage(refusal(e)); } }, [revisionId]);
  useEffect(() => { void api.skills().then(setSkills).catch((e: unknown) => setMessage(refusal(e))); void loadRuns(); }, [loadRuns]);
  useEffect(() => {
    setCard(null); setSpec({}); setMissing({}); setProposal(null); setResult(null); setConfirmed(false);
    if (name) void api.skillCard(name).then(setCard).catch((e: unknown) => setMessage(refusal(e)));
  }, [name]);

  const cleaned = useMemo(() => (prune(spec) ?? {}) as Record<string, unknown>, [spec]);
  const change = (path: string, value: unknown) => { setSpec((s) => setPath(s, path, value)); setConfirmed(false); };
  const act = async (fn: () => Promise<void>) => {
    setBusy(true); setMessage(null);
    try { await fn(); } catch (e) { setMessage(refusal(e)); } finally { setBusy(false); }
  };

  const understand = () => act(async () => {
    const r = await api.skillShortcut(name, sentence);
    setProposal(r);
    setMissing(Object.fromEntries(r.missing.map((m) => [m.field, m.question])));
  });
  const loadProposal = () => {
    if (!proposal) return;
    setSpec(proposal.proposal);
    setConfirmed(false);
  };
  const check = () => act(async () => {
    const r = await api.skillMissing(name, cleaned);
    setMissing(Object.fromEntries(r.missing.map((m) => [m.field, m.question])));
    if (r.missing.length === 0) setMessage("Every required field is filled in.");
  });
  const build = () => act(async () => {
    const r = await api.runSkill(revisionId, name, cleaned);
    setResult(r);
    await loadRuns();
  });
  const useAsModel = (runId: string, which: "ifc" | "dxf") => act(async () => {
    const r = await api.useSkillOutputAsModel(revisionId, runId, which);
    setMessage(`Added to this revision: ${r.spaces} space(s) read. They still need Gate 1 confirmation.`);
  });

  const unavailable = skills.find((s) => s.name === name && !s.available);
  return (
    <main>
      <h1>Drafting</h1>
      <p><a href={`/projects/${projectId}/revisions/${revisionId}/gate1`}>Gate 1</a> | <a href={`/projects/${projectId}/revisions/${revisionId}/diff`}>Diff &amp; results</a></p>
      <label>Skill{" "}
        <select aria-label="Skill" value={name} onChange={(e) => setName(e.target.value)}>
          <option value="">choose…</option>
          {skills.map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
        </select>
      </label>
      {name && <p>{skills.find((s) => s.name === name)?.description}</p>}
      {unavailable && <p role="alert">This skill cannot run on this server: {unavailable.unavailable_reason}</p>}
      <p>Geometry only. Nothing drafted here is checked against any standard, and every dimension is an input you state.</p>

      {card && (
        <>
          <section aria-label="Plain-language shortcut">
            <h2>Describe it in a sentence</h2>
            <textarea aria-label="Describe it" rows={3} cols={70} value={sentence} onChange={(e) => setSentence(e.target.value)}
                      placeholder={name === "duct-fab" ? "600x400 to 300 dia, 500 long, 0.8 mm sheet, pittsburgh seam 25, tdc 30, mark TR-01"
                        : "Level 1, 3.6 m floor to floor; Office 12 x 9 m, 2.7 m high; Plant room 6 x 4 m, 3.2 m high"} />
            <br /><button type="button" disabled={busy || sentence.trim().length < 3} onClick={understand}>Read it</button>
            {proposal && (
              <div data-testid="proposal">
                <h3>What was understood</h3>
                {proposal.understood.length === 0 ? <p>Nothing could be read from that sentence. Fill the card below.</p> : (
                  <table>
                    <thead><tr><th>You wrote</th><th>Field</th><th>Value</th></tr></thead>
                    <tbody>{proposal.understood.map((u, i) => <tr key={i}><td>{u.text}</td><td>{u.field}</td><td>{JSON.stringify(u.value)}</td></tr>)}</tbody>
                  </table>
                )}
                {proposal.assumptions.length > 0 && <ul data-testid="assumptions">{proposal.assumptions.map((a) => <li key={a}><strong>Assumed:</strong> {a}</li>)}</ul>}
                {proposal.missing.length > 0 && (
                  <>
                    <h3>Still needed</h3>
                    <ul data-testid="questions">{proposal.missing.map((m) => <li key={m.field}>{m.question}</li>)}</ul>
                  </>
                )}
                <button type="button" onClick={loadProposal}>Put this into the card</button>
              </div>
            )}
          </section>

          <section aria-label="Spec card">
            <h2>The card</h2>
            <SpecForm fields={card.form} spec={spec} onChange={change} firmDefaults={card.firm_defaults} missing={missing} />
            <p>
              <button type="button" onClick={check} disabled={busy}>What is missing?</button>
            </p>
            <label>
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />{" "}
              I have checked every value above, including the ones read from my sentence.
            </label>
            <p>
              <button type="button" disabled={!confirmed || busy || Boolean(unavailable)} onClick={build}>Build</button>
              {busy && " working…"}
            </p>
          </section>
        </>
      )}

      {message && <p role="alert">{message}</p>}

      {result && (
        <section aria-label="Result" data-testid="skill-result" data-status={result.status}>
          <h2>{result.released ? "Built: all checks passed" : `Not released: ${result.status.replaceAll("_", " ")}`}</h2>
          {result.message && <p>{result.message}</p>}
          {result.validation.checks && (
            <details>
              <summary>{result.validation.checks.filter((c) => c.passed).length} of {result.validation.checks.length} checks passed</summary>
              <ul>{result.validation.checks.map((c) => <li key={c.name}>{c.passed ? "pass" : "FAIL"}: {c.name}</li>)}</ul>
            </details>
          )}
          {result.firm_defaults_applied.length > 0 && <p>Firm defaults used: {result.firm_defaults_applied.join(", ")}</p>}
          {result.released && (
            <ul>
              {result.files.map((f) => (
                <li key={f.artifact_id}>
                  <button type="button" onClick={() => void saveBlob(f.artifact_id, f.name)}>Download {f.name}</button> ({f.bytes} bytes)
                  {name === "space-envelope" && (f.role === "ifc" || f.role === "dxf") && (
                    <> <button type="button" onClick={() => useAsModel(result.run_id, f.role as "ifc" | "dxf")}>Use as this revision&apos;s model</button></>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {drafts.length > 0 && (
        <section aria-label="Cards drafted by the assistant" data-testid="draft-cards">
          <h2>Cards drafted by the assistant</h2>
          <p>The assistant can build only a card version you confirm here.</p>
          <ul>{drafts.map((n) => (
            <li key={n.id}>{n.skill}: {n.body}{" "}
              <button type="button" onClick={() => act(async () => { setReview({ note: n, preview: await api.cardPreview(revisionId, n.skill ?? "", n.spec ?? {}) }); })}>Review this card</button>
            </li>
          ))}</ul>
          {review && (
            <div data-testid="card-review">
              <h3>The exact card that would be built (version {review.preview.spec_sha256.slice(0, 12)})</h3>
              <pre>{JSON.stringify(review.preview.effective_spec, null, 2)}</pre>
              {review.preview.firm_defaults_applied.length > 0 && <p>Firm defaults filled in: {review.preview.firm_defaults_applied.join(", ")}</p>}
              {review.preview.confirmed ? <p>This version is already confirmed.</p> : (
                <button type="button" disabled={busy} onClick={() => act(async () => {
                  await api.confirmCard(revisionId, review.note.skill ?? "", review.note.spec ?? {}, review.preview.spec_sha256, review.note.id);
                  setMessage("Confirmed. The assistant can now build exactly this version."); setReview(null);
                })}>Confirm exactly this version</button>
              )}
            </div>
          )}
        </section>
      )}

      <AgentPanel revisionId={revisionId} onChanged={() => void loadDrafts()} title="Drafting assistant" agents={[...DESIGNER_AGENT]} kinds={DESIGNER_KINDS} />

      <section aria-label="Earlier runs">
        <h2>Earlier runs</h2>
        {runs.length === 0 ? <p>None yet.</p> : (
          <table data-testid="runs">
            <thead><tr><th>When</th><th>Skill</th><th>Outcome</th><th>Files</th></tr></thead>
            <tbody>{runs.map((r) => (
              <tr key={r.run_id}><td>{r.created_at}</td><td>{r.skill}{r.via === "agent" ? " (agent)" : ""}</td>
                <td>{r.passed ? "released" : `not released: ${r.status}`}</td>
                <td>{r.artifacts.filter((a) => a.released).map((a) => a.name).join(", ") || "none"}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </main>
  );
}
