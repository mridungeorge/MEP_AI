"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";
import { applyEdit, canConfirm, canRun, unconfirmedRefs, withEtags } from "@/lib/gate1";
import type { Gate1State, Report, RowKind, RowRef, SpaceInput } from "@/lib/types";
import { HealthPanel } from "./HealthPanel";
import { PartAssignmentPanel } from "./PartAssignmentPanel";
import { PartsEditor } from "./PartsEditor";
import { ProjectFactsPanel } from "./ProjectFactsPanel";
import { RunReport } from "./RunReport";
import { SchedulePanel } from "./SchedulePanel";
import { ManualTraceForm, SpaceTable } from "./SpaceTable";
import { UploadPanel } from "./UploadPanel";

/** Shows API refusals verbatim: code (if any) then message. */
function refusal(e: unknown): string {
  if (e instanceof ApiError) return e.code ? `${e.code}: ${e.message}` : e.message;
  return e instanceof Error ? e.message : String(e);
}

type Selection = Record<RowKind, Set<string>>;
const EMPTY: Selection = { space: new Set(), system_input: new Set(), building_part: new Set(), project: new Set() };

export function Gate1Screen({ revisionId }: { revisionId: string }) {
  const [state, setState] = useState<Gate1State | null>(null);
  const [sel, setSel] = useState<Selection>(EMPTY);
  const [message, setMessage] = useState<string | null>(null);
  const [runMessage, setRunMessage] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);

  const reload = useCallback(async () => {
    try {
      setState(await api.getGate1(revisionId));
    } catch (e) {
      setMessage(refusal(e));
    }
  }, [revisionId]);
  useEffect(() => { void reload(); }, [reload]);

  const selected: RowRef[] = useMemo(
    () => (Object.keys(sel) as RowKind[]).flatMap((kind) => [...sel[kind]].map((id): RowRef => ({ kind, id }))),
    [sel],
  );

  if (!state) return <p>{message ?? "Loading Gate 1..."}</p>;

  const toggle = (kind: RowKind) => (id: string) => {
    const next = new Set(sel[kind]);
    if (next.has(id)) next.delete(id); else next.add(id);
    setSel({ ...sel, [kind]: next });
  };
  const guard = async (fn: () => Promise<void>) => {
    setMessage(null);
    try { await fn(); } catch (e) { setMessage(refusal(e)); }
  };

  const editSpace = (id: string, patch: SpaceInput) =>
    guard(async () => {
      // Edit withdraws confirmation locally; the server trigger does the same.
      setState((s) => s && { ...s, spaces: s.spaces.map((r) => (r.id === id ? applyEdit(r, patch) : r)) });
      try {
        await api.updateSpace(revisionId, id, patch);
      } finally {
        await reload();   // success or refusal: show what the server holds, never the optimistic edit
      }
    });

  const confirm = () =>
    guard(async () => {
      try {
        await api.confirm(revisionId, withEtags(selected, state.spaces, state.inputs, state.parts, state.project));
        setSel(EMPTY);
      } finally {
        await reload();   // a refused confirmation may still have changed what is shown (another user's edit)
      }
    });

  const isDesigner = state.role === "designer";   // fail closed; the server enforces it too
  const run = canRun(state.spaces, state.inputs, state.parts, state.project);
  const confirmOk = isDesigner && canConfirm(state.spaces, state.inputs, selected, state.parts, state.project);
  const lowHealth = state.health === null || state.health.below_threshold;

  return (
    <main>
      <h1>Gate 1: confirm inputs</h1>
      <UploadPanel revisionId={revisionId} onUploaded={reload} />
      <HealthPanel health={state.health} />
      {state.project && (
        <ProjectFactsPanel project={state.project} selected={sel.project.has(state.project.id)}
                           onToggle={() => toggle("project")(state.project!.id)} />
      )}
      <PartsEditor
        key={state.parts.map((p) => `${p.id}:${p.confirmed}`).join(",")}
        parts={state.parts}
        selected={sel.building_part}
        onToggle={toggle("building_part")}
        onSave={async (p) => { await api.putParts(revisionId, p); await reload(); }}
      />
      <SpaceTable spaces={state.spaces} selected={sel.space} onToggle={toggle("space")} onEdit={editSpace} />
      {lowHealth && (
        <ManualTraceForm onAdd={async (s) => { await api.addSpace(revisionId, s); await reload(); }} />
      )}
      <SchedulePanel
        inputs={state.inputs}
        selected={sel.system_input}
        onToggle={toggle("system_input")}
        templateUrl={api.templateUrl(state.ncc_edition)}
        onAdd={async (i) => { await api.addInput(revisionId, i); await reload(); }}
        onImport={async (f) => { await api.importSchedule(revisionId, f); await reload(); }}
      />
      <PartAssignmentPanel
        parts={state.parts}
        inputs={state.inputs}
        onAssign={async (tag, part) => { await guard(async () => { await api.assignPart(revisionId, tag, part); await reload(); }); }}
      />
      <section aria-label="Confirm">
        <button type="button"
                onClick={() => setSel({
                  ...EMPTY,
                  ...Object.fromEntries((["space", "system_input", "building_part", "project"] as RowKind[]).map(
                    (k) => [k, new Set(unconfirmedRefs(state.spaces, state.inputs, state.parts, state.project)
                      .filter((r) => r.kind === k).map((r) => r.id))])) as Selection,
                })}>
          Select all unconfirmed
        </button>{" "}
        <button type="button" disabled={!confirmOk} onClick={confirm}>Confirm selected ({selected.length})</button>
        {!isDesigner && <span> Only a designer confirms Gate 1 rows.</span>}
        {isDesigner && !confirmOk && selected.length > 0 && <span> A selected row has no value.</span>}
        {message && <p role="alert" style={{ color: "#b91c1c" }}>{message}</p>}
      </section>
      <section aria-label="Run rules">
        <button
          type="button"
          disabled={!run.ok}
          onClick={async () => {
            setRunMessage(null);
            setReport(null);
            try {
              const r = await api.runRules(revisionId);
              setRunMessage(`Run complete: ${r.run_id}`);
              setReport(r.report);
            } catch (e) {
              setRunMessage(refusal(e));
            }
          }}
        >
          Run rules
        </button>
        {!run.ok && <p data-testid="run-blocked">{run.reason}</p>}
        {runMessage && <p role="alert">{runMessage}</p>}
      </section>
      {report && <RunReport report={report} />}
    </main>
  );
}
