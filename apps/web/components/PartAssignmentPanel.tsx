"use client";
import { useState } from "react";
import type { BuildingPart, SystemInputRow } from "@/lib/types";

/** In a mixed-use project every system names the building part it serves. The choice is a schedule input like any other:
 *  it lands as 'default', a designer confirms it, and changing the building parts withdraws the confirmation. */
export function PartAssignmentPanel({
  parts, inputs, onAssign,
}: {
  parts: BuildingPart[];
  inputs: SystemInputRow[];
  onAssign: (tag: string, part: number) => Promise<void>;
}) {
  const [error, setError] = useState<string | null>(null);
  if (parts.length < 2) return null;
  const tags = [...new Set(inputs.map((i) => i.system).filter((t): t is string => !!t))];
  if (tags.length === 0) return null;
  const current = (tag: string): string => {
    const row = inputs.find((i) => i.system === tag && i.name === "building_part");
    return typeof row?.value === "number" ? String(row.value) : "";
  };
  return (
    <section aria-label="Building part of each system">
      <h2>Building part of each system</h2>
      {tags.map((tag) => (
        <p key={tag}>
          <label>
            {tag}{" "}
            <select
              aria-label={`Building part for ${tag}`}
              value={current(tag)}
              onChange={(e) => {
                setError(null);
                onAssign(tag, Number(e.target.value)).catch((err: unknown) =>
                  setError(err instanceof Error ? err.message : String(err)));
              }}
            >
              <option value="">Choose…</option>
              {parts.map((p, i) => (
                <option key={p.id} value={i}>Part {i + 1}: class {p.building_class}, {p.storeys} storeys</option>
              ))}
            </select>
          </label>
        </p>
      ))}
      {error && <p role="alert" style={{ color: "#b91c1c" }}>{error}</p>}
    </section>
  );
}
