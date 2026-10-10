"use client";
import { useCallback, useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { AgentNote } from "@/lib/types";

function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

type AgentChoice = { id: "designer" | "adversarial_checker" | "compliance_risk"; label: string; prompt: string };

/** Ask a runtime agent about this revision and read the notes agents have left. An agent can only READ and leave notes (flags, risks, questions,
 *  hypotheses, explanations): it cannot approve, confirm, sign or set any outcome, and nothing it writes is a result. People close the notes. */
export function AgentPanel({ revisionId, agents, kinds, title }: { revisionId: string; agents: AgentChoice[]; kinds: string[]; title: string }) {
  const [configured, setConfigured] = useState<{ configured: boolean; reason: string } | null>(null);
  const [notes, setNotes] = useState<AgentNote[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const all = await api.agentNotes(revisionId);
      setNotes(all.filter((n) => kinds.includes(n.kind)));
    } catch (e) { setMessage(refusal(e)); }
  }, [revisionId, kinds]);
  useEffect(() => { void api.agentsStatus().then(setConfigured).catch(() => setConfigured({ configured: false, reason: "unavailable" })); void load(); }, [load]);

  const ask = async (agent: AgentChoice["id"], prompt: string) => {
    setBusy(true); setMessage(null); setReply(null);
    try {
      const r = await api.askAgent(revisionId, agent, prompt);
      setReply(r.reply || "(no reply)");
      if (r.calls.some((c) => c.denied)) setMessage(`${r.calls.filter((c) => c.denied).length} request(s) by the agent were refused by the server.`);
      await load();
    } catch (e) { setMessage(refusal(e)); } finally { setBusy(false); }
  };
  const close = async (id: string, status: "answered" | "dismissed") => {
    try { await api.resolveAgentNote(revisionId, id, status); await load(); } catch (e) { setMessage(refusal(e)); }
  };
  const open = notes.filter((n) => n.status === "open");

  return (
    <section aria-label={title} data-testid="agent-panel">
      <h2>{title}</h2>
      <p>Agents only read and leave notes. They cannot approve, confirm, sign or change a result.</p>
      {configured && !configured.configured && <p data-testid="agents-off">Agents are switched off on this server: {configured.reason}.</p>}
      {agents.map((a) => (
        <p key={a.id}>
          {a.id === "designer" ? (
            <>
              <input aria-label="Ask the drafting assistant" value={text} maxLength={2000} size={70} placeholder={a.prompt} onChange={(e) => setText(e.target.value)} />{" "}
              <button type="button" disabled={busy || !configured?.configured || text.trim().length < 2} onClick={() => void ask(a.id, text.trim())}>{a.label}</button>
            </>
          ) : (
            <button type="button" disabled={busy || !configured?.configured} onClick={() => void ask(a.id, a.prompt)}>{a.label}</button>
          )}
        </p>
      ))}
      {busy && <p>The agent is working…</p>}
      {reply && <p data-testid="agent-reply"><strong>Assistant&apos;s note (not a rule result):</strong> {reply}</p>}
      {message && <p role="alert">{message}</p>}
      {open.length > 0 && (
        <ul data-testid="agent-notes">
          {open.map((n) => (
            <li key={n.id} data-kind={n.kind}>
              <strong>{n.kind.replaceAll("_", " ")}{n.severity ? ` (${n.severity})` : ""}</strong>{n.post_freeze ? <em> [post-freeze annotation: not part of the signed package]</em> : null} from the {n.agent.replaceAll("_", " ")} agent: {n.body}{" "}
              <button type="button" onClick={() => void close(n.id, n.kind === "clarifying_question" ? "answered" : "dismissed")}>
                {n.kind === "clarifying_question" ? "Mark answered" : "Dismiss"}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
