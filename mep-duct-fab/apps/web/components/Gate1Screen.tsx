"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { applyEdit, canConfirm, canRun } from "@/lib/gate1";
import type { Gate1State, RowRef, SpaceInput } from "@/lib/types";
import { HealthPanel } from "./HealthPanel";
import { PartsEditor } from "./PartsEditor";
import { SchedulePanel } from "./SchedulePanel";
import { ManualTraceForm, SpaceTable } from "./SpaceTable";

/** Shows API refusals verbatim: code (if any) then message. */
function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

export function Gate1Screen({ revisionId }: { revisionId: string }) {
  const [state, setState] = useState<Gate1State | null>(null);
  const [selSpaces, setSelSpaces] = useState<Set<string>>(new Set());
  const [selInputs, setSelInputs] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setState(await api.getGate1(revisionId));
    } catch (e) {
      setMessage(refusal(e));
    }
  }, [revisionId]);
  useEffect(() => { void reload(); }, [reload]);

  const selected: RowRef[] = useMemo(
    () => [
      ...[...selSpaces].map((id): RowRef => ({ kind: "space", id })),
      ...[...selInputs].map((id): RowRef => ({ kind: "system_input", id })),
    ],
    [selSpaces, selInputs],
  );

  if (!state) return <p>{message ?? "Loading Gate 1..."}</p>;

  const toggle = (set: Set<string>, setter: (s: Set<string>) => void) => (id: string) => {
    const n = new Set(set);
    if (n.has(id)) n.delete(id); else n.add(id);
    setter(n);
  };
  const guard = async (fn: () => Promise<void>) => {
    setMessage(null);
    try { await fn(); } catch (e) { setMessage(refusal(e)); }
  };

  const editSpace = (id: string, patch: SpaceInput) =>
    guard(async () => {
      // Edit withdraws confirmation locally; the server trigger does the same.
      setState((s) => s && { ...s, spaces: s.spaces.map((r) => (r.id === id ? applyEdit(r, patch) : r)) });
      await api.updateSpace(revisionId, id, patch);
      await reload();
    });

  const confirm = () =>
    guard(async () => {
      await api.confirm(revisionId, selected);
      setSelSpaces(new Set());
      setSelInputs(new Set());
      await reload();
    });

  const run = canRun(state.spaces, state.inputs);
  const confirmOk = canConfirm(state.spaces, state.inputs, selected);

  return (
    <main>
      <h1>Gate 1: confirm inputs</h1>
      <HealthPanel health={state.health} />
      <PartsEditor
        key={state.parts.map((p) => p.id).join(",")}
        parts={state.parts}
        onSave={async (p) => { await api.putParts(revisionId, p); await reload(); }}
      />
      <SpaceTable spaces={state.spaces} selected={selSpaces} onToggle={toggle(selSpaces, setSelSpaces)} onEdit={editSpace} />
      {state.health.below_threshold && (
        <ManualTraceForm onAdd={async (s) => { await api.addSpace(revisionId, s); await reload(); }} />
      )}
      <SchedulePanel
        inputs={state.inputs}
        selected={selInputs}
        onToggle={toggle(selInputs, setSelInputs)}
        templateUrl={api.templateUrl(state.ncc_edition)}
        onAdd={async (i) => { await api.addInput(revisionId, i); await reload(); }}
        onImport={async (f) => { await api.importSchedule(revisionId, f); await reload(); }}
      />
      <section aria-label="Confirm">
        <button type="button" disabled={!confirmOk} onClick={confirm}>Confirm selected ({selected.length})</button>
        {!confirmOk && selected.length > 0 && <span> A selected row has no value.</span>}
        {message && <p role="alert" style={{ color: "#b91c1c" }}>{message}</p>}
      </section>
      <section aria-label="Run rules">
        <button
          type="button"
          disabled={!run.ok}
          onClick={async () => {
            setRunMessage(null);
            try {
              const r = await api.runRules(revisionId);
              setRunMessage(`Run started: ${r.run_id}`);
            } catch (e) {
              setRunMessage(refusal(e));
            }
          }}
        >
          Run rules
        </button>
        {!run.ok && <p>{run.reason}</p>}
        {runMessage && <p role="alert">{runMessage}</p>}
      </section>
    </main>
  );
}
