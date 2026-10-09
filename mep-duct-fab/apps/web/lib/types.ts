/**
 * Types mirroring the API shapes for Gate 1. Presentation only: the UI never
 * decides compliance; refusal codes and messages come from the API verbatim.
 */

/** Where a value came from. Clients may only write 'default'; the server sets 'engineer_confirmed' on confirm. */
export type Provenance = "extracted" | "default" | "engineer_confirmed";

/** Closed list of NCC building classes. */
export const BUILDING_CLASSES = ["2", "3", "4", "5", "6", "7a", "7b", "8", "9a", "9b", "9c"] as const;
export type BuildingClass = (typeof BUILDING_CLASSES)[number];

/** A building part: class, storeys (1-200), floor area in m2 (> 0). */
export interface BuildingPart {
  id: string;
  building_class: BuildingClass;
  storeys: number;
  area_m2: number;
}

/** A space row. Null value fields mean "no value yet". Provenance is per row (applies to each value). */
export interface SpaceRow {
  id: string;
  /** Null for a manual-trace space (no IFC source). */
  ifc_guid: string | null;
  name: string | null;
  area_m2: number | null;
  use: string | null;
  storey: number | null;
  ceiling_void_mm: number | null;
  provenance: Provenance;
  /** True when the designer added this by hand because ingest health was low. */
  manual_trace: boolean;
}

/** A system schedule input (name, value, unit). */
export interface SystemInputRow {
  id: string;
  name: string;
  value: number | string | null;
  unit: string | null;
  provenance: Provenance;
}

/** Ingest health, computed by the API. The UI only displays it. */
export interface IngestHealth {
  /** 0-100 */
  score_percent: number;
  threshold_percent: number;
  /** True when the API says the score is below threshold. */
  below_threshold: boolean;
  /** Human-readable fixes the designer can make. */
  fixes: string[];
}

/** GET /revisions/{id}/gate1 */
export interface Gate1State {
  parts: BuildingPart[];
  spaces: SpaceRow[];
  inputs: SystemInputRow[];
  health: IngestHealth;
  /** NCC edition of the project; selects the schedule template, e.g. "2022". */
  ncc_edition: string;
}

export type RowKind = "space" | "system_input";
export interface RowRef {
  kind: RowKind;
  id: string;
}

/** POST /revisions/{id}/gate1/confirm body. */
export interface ConfirmRequest {
  rows: RowRef[];
}
/** Confirm success: the rows now engineer_confirmed. */
export interface ConfirmResponse {
  confirmed: RowRef[];
}

/** Engine refusal codes, surfaced verbatim. */
export type RefusalCode = "extracted_inputs" | "unconfirmed_facts" | "gate1_required";

/** Error body from the API (non-2xx). `code` may be a refusal code or other string. */
export interface ApiErrorBody {
  code?: string;
  message?: string;
  detail?: string | { code?: string; message?: string };
}

/** POST /revisions/{id}/gate1/schedule/import response. */
export interface ImportResponse {
  inputs: SystemInputRow[];
  warnings?: string[];
}

/** POST /revisions/{id}/run-rules response (opaque run reference). */
export interface RunRulesResponse {
  run_id: string;
}

/** Edit payloads. Provenance is not sendable: the server forces 'default' on any edit. */
export type SpaceInput = Partial<Omit<SpaceRow, "id" | "provenance">>;
export type SystemInputInput = Partial<Omit<SystemInputRow, "id" | "provenance">>;
