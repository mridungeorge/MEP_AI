"use client";
import { useState } from "react";
import { rowState } from "@/lib/gate1";
import type { SystemInputInput, SystemInputRow } from "@/lib/types";
import { ProvenanceBadge } from "./ProvenanceBadge";

export function SchedulePanel({
  inputs, selected, onToggle, onAdd, onImport, templateUrl,
}: {
  inputs: SystemInputRow[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onAdd: (i: SystemInputInput) => Promise<void>;
  onImport: (file: File) => Promise<void>;
  templateUrl: string;
}) {
  const tags = [...new Set(inputs.map((i) => i.system).filter((t): t is string => !!t))];
  const [system, setSystem] = useState("");
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function run(fn: () => Promise<void>) {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <section aria-label="System schedule">
      <h2>System schedule inputs</h2>
      <table>
        <thead>
          <tr><th>Select</th><th>System</th><th>Name</th><th>Value</th><th>Unit</th><th>Provenance</th></tr>
        </thead>
        <tbody>
          {inputs.map((i) => (
            <tr key={i.id} data-state={rowState(i)} data-testid="input-row">
              <td><input type="checkbox" checked={selected.has(i.id)} onChange={() => onToggle(i.id)} aria-label={`Select ${i.system ?? ""} ${i.name}`} /></td>
              <td>{i.system ?? ""}</td>
              <td>{i.name}</td>
              <td>{i.name === "building_part" && typeof i.value === "number" ? `Part ${i.value + 1}` : typeof i.value === "boolean" ? String(i.value) : (i.value ?? "")}</td>
              <td>{i.unit ?? ""}</td>
              <td><ProvenanceBadge provenance={i.provenance} /></td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Add input</h3>
      <select aria-label="System tag" value={system} onChange={(e) => setSystem(e.target.value)}>
        <option value="">System…</option>
        {tags.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>{" "}
      <input aria-label="Name" placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} />{" "}
      <input aria-label="Value" placeholder="Value" value={value} onChange={(e) => setValue(e.target.value)} />{" "}
      <input aria-label="Unit" placeholder="Unit (e.g. L/s)" value={unit} onChange={(e) => setUnit(e.target.value)} />{" "}
      <button
        type="button"
        onClick={() =>
          run(async () => {
            if (!system) throw new Error("Choose the system this input belongs to (import the schedule first).");
            if (!name.trim() || value.trim() === "") throw new Error("Enter a name and a value.");
            const n = Number(value);
            await onAdd({ system, name: name.trim(), value: Number.isFinite(n) ? n : value, unit: unit.trim() || null });
            setName(""); setValue(""); setUnit("");
          })
        }
      >
        Add
      </button>
      <h3>Excel import</h3>
      <p><a href={templateUrl} download>Download the schedule template</a></p>
      <input
        type="file"
        accept=".xlsx"
        aria-label="Upload schedule"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void run(() => onImport(f));
          e.target.value = "";
        }}
      />
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
