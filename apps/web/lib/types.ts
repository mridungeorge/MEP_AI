/**
 * Types mirroring the API shapes for Gate 1. Presentation only: the UI never
 * decides compliance; refusal codes and messages come from the API verbatim.
 */

/** Where a value came from. Clients may only write 'default'; the server sets 'engineer_confirmed' on confirm. */
export type Provenance = "extracted" | "default" | "engineer_confirmed";

/** Closed list of NCC building classes. */
export const BUILDING_CLASSES = ["2", "3", "4", "5", "6", "7a", "7b", "8", "9a", "9b", "9c"] as const;
export type BuildingClass = (typeof BUILDING_CLASSES)[number];

/** A building part: class, storeys (1-200), floor area in m2 (> 0). `confirmed` is the server's record of Gate 1. */
export interface BuildingPart {
  id: string;
  building_class: BuildingClass;
  storeys: number;
  area_m2: number;
  confirmed?: boolean;
  /** Version of the row the server showed; confirming names it so a changed row is refused. */
  etag?: string;
}

/** A space row. Null value fields mean "no value yet". Provenance is per row (applies to each value). */
export interface SpaceRow {
  id: string;
  /** Null for a manual-trace space (no IFC source). */
  ifc_guid: string | null;
  name: string | null;
  area_m2: number | null;
  use: string | null;
  /** IFC storeys are names ("00 groundfloor"); a hand-entered storey may be a number. */
  storey: number | string | null;
  ceiling_void_mm: number | null;
  provenance: Provenance;
  /** True when the designer added this by hand because ingest health was low. */
  manual_trace: boolean;
  etag?: string;
}

/** A system schedule input (name, value, unit) of the system with this schedule tag. */
export interface SystemInputRow {
  id: string;
  /** Schedule tag of the owning system. */
  system?: string | null;
  name: string;
  value: number | string | boolean | null;
  unit: string | null;
  provenance: Provenance;
  etag?: string;
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

/** The project facts that choose the rule pack. A designer confirms them like any other Gate 1 value. */
export interface ProjectFacts {
  id: string;
  state: string;
  ncc_edition: string;
  climate_zone: number | null;
  approval_date: string | null;
  confirmed: boolean;
  etag?: string;
}

/** GET /revisions/{id}/gate1 */
export interface Gate1State {
  parts: BuildingPart[];
  spaces: SpaceRow[];
  inputs: SystemInputRow[];
  /** Null when nothing has been ingested yet. */
  health: IngestHealth | null;
  /** NCC edition of the project, e.g. "NCC2025"; selects the schedule template. */
  ncc_edition: string;
  project?: ProjectFacts;
  /** The signed-in user's role. Only a designer may confirm; the server enforces it. */
  role?: string;
}

export type RowKind = "space" | "system_input" | "building_part" | "project";
export interface RowRef {
  kind: RowKind;
  id: string;
  /** The row version the designer was shown (copied from the row). */
  etag?: string;
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

/** POST /revisions/{id}/schedule/import response. */
export interface ImportResponse {
  systems: number;
  inputs: number;
}

/** One cited rule result in the report. The outcome is the engine's; the UI only shows it. */
export interface ReportResult {
  subject_id: string;
  rule_id: string;
  outcome: string;
  causes?: string[];
  citation: { document: string; edition: string; clause: string; url: string; rule_status: string };
}

/** The cited report the engine returns. `banner` is set while any rule used is a draft. */
export interface Report {
  banner: string | null;
  draft_rules?: string[];
  /** Rules selected for the project but run on no system (the engine lists them). */
  unassigned_rules?: string[];
  jurisdiction?: { decision?: string; notes?: string[]; warnings?: string[]; refused_by?: string[] };
  results: ReportResult[];
}

/** POST /revisions/{id}/run-rules response. */
export interface RunRulesResponse {
  run_id: string;
  report: Report;
}

/** Edit payloads. Provenance is not sendable: the server forces 'default' on any edit. */
export type SpaceInput = Partial<Omit<SpaceRow, "id" | "provenance">>;
export type SystemInputInput = Partial<Omit<SystemInputRow, "id" | "provenance">>;

/** GET /me: the role and firm are the database's, not the token's. */
export interface Me {
  user_id: string;
  role: string;
  firm_id: string;
  firm_name: string | null;
}

/** GET /revisions: the revisions of the signed-in user's firm. */
export interface RevisionSummary {
  id: string;
  project_id: string;
  address: string;
  state: string;
  ncc_edition: string;
  architect_rev: string;
  status: string;
  frozen: boolean;
  parent_revision_id: string | null;
}

/** POST /revisions/{id}/uploads */
export interface UploadResponse {
  kind: "ifc" | "dxf";
  name: string;
  sha256: string;
  bytes: number;
  storage_path: string;
  ingest_run: string;
  spaces: number;
  extractions: number;
  health: IngestHealth | null;
  problems: string[];
  /** The revision the file was ingested into (a child when the target was frozen). */
  revision_id: string;
  new_revision?: NewRevision;
}

/** POST /revisions/{id}/uploads on a FROZEN revision creates a child revision. */
export interface NewRevision {
  id: string;
  project_id: string;
  architect_rev: string;
  parent_revision_id: string;
  spaces_carried_unchanged: number;
}

export interface FieldChange { field: string; old: unknown; new: unknown }
export interface SpaceFields {
  ifc_guid: string | null; name: string | null; area_m2: number | null; use: string | null;
  storey: number | string | null; ceiling_void_mm: number | null;
}
export interface SpaceDiffItem {
  change: "added" | "removed" | "changed";
  key: string;
  matched_by: string | null;
  fields: FieldChange[];
  space_id: string | null;
  confirmed: boolean | null;
  old: SpaceFields | null;
  new: SpaceFields | null;
  name: string | null;
}
export interface InputDiffItem {
  change: string; system: string; name: string; old: unknown; new: unknown;
  unit_old: string | null; unit_new: string | null; is_part_change: boolean;
}
export interface TraceStep { kind: string; id: string; text: string; via: string; path: string }
export interface TraceItem {
  subject_id: string; rule_id: string; chains: TraceStep[][]; lines: string[]; before: string | null; after: string | null;
}
export interface ConflictItem { subject_id: string; input: string; better_rule: string; worse_rule: string; text: string }
/** GET /revisions/{id}/diff: what changed from the parent, which results it makes stale, and why. */
export interface RevisionDiff {
  parent: { id: string; architect_rev: string } | null;
  spaces: SpaceDiffItem[];
  inputs: InputDiffItem[];
  stale: { subject_id: string; rule_id: string }[];
  traces: TraceItem[];
  conflicts?: ConflictItem[];
  hash: string | null;
  confirmed: boolean;
  needs_confirmation: string[];
  has_changes: boolean;
}
export interface LineageBrief { id: string; architect_rev: string; status: string; frozen: boolean }
export interface Lineage { revision: LineageBrief; ancestors: LineageBrief[]; children: LineageBrief[] }
export interface StoredResult {
  subject_id: string; rule_id: string; outcome: string; part: number | null; run_id: string | null;
  citation: unknown; causes: unknown; stale: boolean;
}
export interface RevisionResults { source: "own" | "carried_from_parent"; parent_revision_id?: string; results: StoredResult[] }

/** Gate 2 worksheet (GET /revisions/{id}/review). Classes, reasons and decisions are the API's. */
export interface ReviewLine {
  id: string; subject_id: string; rule_id: string; part: number | null; outcome: string;
  citation: { document?: string; clause?: string; rule_status?: string; url?: string };
  review_class: string | null; reasons: string[]; stale: boolean; fix_hypotheses: string[];
  decision: "approve" | "reject" | "request_changes" | null; reason: string | null;
  bulk: boolean; spot_check: boolean; in_sample: boolean;
}
export interface SignoffView { gate: string; role: string; signed_at: string; registration_no: string | null; mine: boolean }
export interface Worksheet {
  revision: { id: string; architect_rev: string; frozen: boolean };
  results: ReviewLine[]; by_class: Record<string, number>; open_clean: number; approved: number; total: number;
  signoffs: SignoffView[];
  sample: { id: string; size: number; of: number; result_ids: string[] } | null;
}
export interface ShareLinkView { id: string; created_at: string; expires_at: string; revoked: boolean; views: number; label: string | null; last_viewed_at: string | null }
/** GET /revisions/{id}/package and GET /share/{token}. */
export interface Package {
  banner: string | null;
  revision: { id: string; architect_rev: string; status: string; frozen_at: string | null; derived_from: string[] };
  project: { address: string; state: string; climate_zone: number; ncc_edition: string; approval_date: string;
             building_parts: { class: string; storeys: number; area_m2: number }[] };
  summary: Record<string, number>;
  results: { subject: string; rule_id: string; part: number | null; outcome: string; citation: ReviewLine["citation"];
             review_class: string | null; reasons: string[]; decision: string | null; reason: string | null; bulk: boolean;
             reviewed_by: string | null }[];
  signoffs: { gate: string; role: string; email: string | null; signed_at: string; registration_no: string | null; attests: string | null }[];
  status: { signed_gates: string[]; complete: boolean; missing: string[] };
  ledger: { verified: boolean; events: number; reason: string | null; anchor_seq: number | null; anchor_hash: string | null };
}
