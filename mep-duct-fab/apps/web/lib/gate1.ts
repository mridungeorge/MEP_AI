/** Pure Gate 1 row helpers. No compliance logic: only row state and button gating. */
import type { BuildingPart, Provenance, RowRef, SpaceRow, SystemInputRow } from "./types";
import { BUILDING_CLASSES } from "./types";

export type Row = SpaceRow | SystemInputRow;
export type RowState = "unconfirmed" | "confirmed";

export function rowState(row: { provenance: Provenance }): RowState {
  return row.provenance === "engineer_confirmed" ? "confirmed" : "unconfirmed";
}

/** Matches the DB trigger: any edit withdraws confirmation; clients may only write 'default'. */
export function applyEdit<T extends Row>(row: T, patch: Partial<Omit<T, "id" | "provenance">>): T {
  return { ...row, ...patch, provenance: "default" };
}

function present(v: unknown): boolean {
  return v !== null && v !== undefined && !(typeof v === "string" && v.trim() === "");
}

/** Space needs name, area, storey; system input needs name and value (unit is checked by the API). */
export function hasValue(row: Row, kind: "space" | "system_input"): boolean {
  if (kind === "space") {
    const s = row as SpaceRow;
    return present(s.name) && present(s.area_m2) && present(s.storey);
  }
  const i = row as SystemInputRow;
  return present(i.name) && present(i.value);
}

/** Confirm is allowed only with a non-empty selection where every selected row has a value. */
export function canConfirm(spaces: SpaceRow[], inputs: SystemInputRow[], selected: RowRef[]): boolean {
  if (selected.length === 0) return false;
  return selected.every((ref) => {
    const row = ref.kind === "space" ? spaces.find((s) => s.id === ref.id) : inputs.find((i) => i.id === ref.id);
    return row !== undefined && hasValue(row, ref.kind);
  });
}

export interface CanRun {
  ok: boolean;
  reason: string | null;
}

/** Run is enabled only when there are rows and all of them are confirmed. */
export function canRun(spaces: SpaceRow[], inputs: SystemInputRow[]): CanRun {
  const all: Row[] = [...spaces, ...inputs];
  if (all.length === 0) return { ok: false, reason: "No spaces or system inputs yet." };
  const n = all.filter((r) => rowState(r) !== "confirmed").length;
  if (n > 0) return { ok: false, reason: `${n} row(s) are not confirmed. Confirm every row used before running rules.` };
  return { ok: true, reason: null };
}

/** Client-side input sanity only (the API re-validates). Returns an error message or null. */
export function validatePart(p: Pick<BuildingPart, "building_class" | "storeys" | "area_m2">): string | null {
  if (!(BUILDING_CLASSES as readonly string[]).includes(p.building_class)) return "Choose a building class from the list.";
  if (!Number.isInteger(p.storeys) || p.storeys < 1 || p.storeys > 200) return "Storeys must be a whole number from 1 to 200.";
  if (!(p.area_m2 > 0)) return "Area must be greater than 0 m2.";
  return null;
}
