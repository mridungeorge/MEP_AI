/** Pure Gate 1 row helpers. No compliance logic: only row state and button gating. */
import type { BuildingPart, Provenance, ProjectFacts, RowRef, SpaceRow, SystemInputRow } from "./types";
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

/**
 * Confirm is allowed only with a non-empty selection where every selected space or input has a value and every
 * selected part or the project facts exist. The server re-checks all of it (and the designer role).
 */
export function canConfirm(
  spaces: SpaceRow[], inputs: SystemInputRow[], selected: RowRef[],
  parts: BuildingPart[] = [], project: ProjectFacts | undefined = undefined,
): boolean {
  if (selected.length === 0) return false;
  return selected.every((ref) => {
    if (ref.kind === "building_part") return parts.some((p) => p.id === ref.id);
    if (ref.kind === "project") return project !== undefined && project.id === ref.id;
    const row = ref.kind === "space" ? spaces.find((s) => s.id === ref.id) : inputs.find((i) => i.id === ref.id);
    return row !== undefined && hasValue(row, ref.kind);
  });
}

export interface CanRun {
  ok: boolean;
  reason: string | null;
}

/**
 * Run is enabled only when there are rows and all of them are confirmed. When `parts` / `project` are passed they must
 * be confirmed too (the server refuses the run otherwise).
 */
export function canRun(
  spaces: SpaceRow[], inputs: SystemInputRow[],
  parts: BuildingPart[] | undefined = undefined, project: ProjectFacts | undefined = undefined,
): CanRun {
  const all: Row[] = [...spaces, ...inputs];
  if (all.length === 0) return { ok: false, reason: "No spaces or system inputs yet." };
  const n = all.filter((r) => rowState(r) !== "confirmed").length;
  if (n > 0) return { ok: false, reason: `${n} row(s) are not confirmed. Confirm every row used before running rules.` };
  if (parts !== undefined) {
    if (parts.length === 0) return { ok: false, reason: "Enter the building parts and confirm them." };
    const u = parts.filter((p) => !p.confirmed).length;
    if (u > 0) return { ok: false, reason: `${u} building part(s) are not confirmed.` };
  }
  if (project !== undefined && !project.confirmed) {
    return { ok: false, reason: "The project facts (state, NCC edition, climate zone, approval date) are not confirmed." };
  }
  return { ok: true, reason: null };
}

/** Everything the designer has not confirmed yet, as a selection (used by "Select all unconfirmed"). */
export function unconfirmedRefs(
  spaces: SpaceRow[], inputs: SystemInputRow[], parts: BuildingPart[], project: ProjectFacts | undefined,
): RowRef[] {
  return [
    ...(project && !project.confirmed ? [{ kind: "project", id: project.id, etag: project.etag } as RowRef] : []),
    ...parts.filter((p) => !p.confirmed).map((p): RowRef => ({ kind: "building_part", id: p.id, etag: p.etag })),
    ...spaces.filter((s) => rowState(s) !== "confirmed").map((s): RowRef => ({ kind: "space", id: s.id, etag: s.etag })),
    ...inputs.filter((i) => rowState(i) !== "confirmed").map((i): RowRef => ({ kind: "system_input", id: i.id, etag: i.etag })),
  ];
}

/** Attach the row version the screen is showing to each selected ref (the server refuses a changed row). */
export function withEtags(
  refs: RowRef[], spaces: SpaceRow[], inputs: SystemInputRow[], parts: BuildingPart[], project: ProjectFacts | undefined,
): RowRef[] {
  return refs.map((r) => {
    const row =
      r.kind === "space" ? spaces.find((s) => s.id === r.id)
      : r.kind === "system_input" ? inputs.find((i) => i.id === r.id)
      : r.kind === "building_part" ? parts.find((p) => p.id === r.id)
      : project?.id === r.id ? project : undefined;
    return { ...r, etag: row?.etag };
  });
}

/** Client-side input sanity only (the API re-validates). Returns an error message or null. */
export function validatePart(p: Pick<BuildingPart, "building_class" | "storeys" | "area_m2">): string | null {
  if (!(BUILDING_CLASSES as readonly string[]).includes(p.building_class)) return "Choose a building class from the list.";
  if (!Number.isInteger(p.storeys) || p.storeys < 1 || p.storeys > 200) return "Storeys must be a whole number from 1 to 200.";
  if (!(p.area_m2 > 0)) return "Area must be greater than 0 m2.";
  return null;
}
