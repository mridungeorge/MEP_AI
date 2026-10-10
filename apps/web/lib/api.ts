/** Typed API client. Every call goes through apiFetch. */
import { supabase } from "./supabase";
import type {
  ApiErrorBody, BuildingPart, ConfirmResponse, Gate1State, ImportResponse, RowRef,
  AdminOverview, MyInvitation, AgentNote, AgentReply, VisionJob, CardPreview, EvidenceResponse, Lineage, Me, ShortcutResult, SkillCard, SkillRunRow, SkillRunSummary, SkillSummary, Package, ShareLinkView, Worksheet, RevisionDiff, RevisionResults, RevisionSummary, RunRulesResponse, SpaceInput, SpaceRow, SystemInputInput, SystemInputRow, UploadResponse,
} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The access token of the signed-in session (the API verifies it and looks the role up itself; nothing is read from it here). */
export async function getToken(): Promise<string | null> {
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? null;
}

/** Small firms: the role the person has chosen to act in (the server checks it against the roles the firm gave them). */
const ACTING_KEY = "mep.acting_role";
export function getActingRole(): string | null {
  try { return window.localStorage.getItem(ACTING_KEY); } catch { return null; }
}
export function setActingRole(role: string | null): void {
  try { if (role) window.localStorage.setItem(ACTING_KEY, role); else window.localStorage.removeItem(ACTING_KEY); } catch { /* storage blocked */ }
}

/** Error carrying the API's code and message verbatim. */
export class ApiError extends Error {
  constructor(public status: number, public code: string | null, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const headers = new Headers(init.headers);
  const acting = getActingRole();
  if (acting) headers.set("X-Acting-Role", acting);
  const token = await getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  if (res.status === 403 && acting && !retried) {
    // the chosen acting role is no longer permitted (the firm's mode or the person's roles changed): forget it and retry as themselves
    const body = (await res.clone().json().catch(() => ({}))) as ApiErrorBody;
    if ((body.detail as { code?: string } | undefined)?.code === "not_permitted_role") {
      setActingRole(null);
      return apiFetch<T>(path, init, true);
    }
  }
  if (!res.ok) {
    let body: ApiErrorBody = {};
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      /* non-JSON error body */
    }
    // FastAPI may nest the error as {detail: {code, message}}.
    const d = body.detail;
    const nested = d && typeof d === "object" ? d : null;
    const code = body.code ?? nested?.code ?? null;
    const message = body.message ?? nested?.message ?? (typeof d === "string" ? d : `HTTP ${res.status}`);
    throw new ApiError(res.status, code, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const rev = (id: string) => `/revisions/${encodeURIComponent(id)}`;
const json = (method: string, body: unknown): RequestInit => ({ method, body: JSON.stringify(body) });

export const api = {
  me: () => apiFetch<Me>("/me"),
  revisions: () => apiFetch<RevisionSummary[]>("/revisions"),
  upload: (r: string, file: File, architectRev?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    if (architectRev) fd.append("architect_rev", architectRev);
    return apiFetch<UploadResponse>(`${rev(r)}/uploads`, { method: "POST", body: fd });
  },
  assignPart: (r: string, tag: string, part: number) =>
    apiFetch<SystemInputRow>(`${rev(r)}/gate1/systems/${encodeURIComponent(tag)}/part`, json("PUT", { part })),
  getGate1: (r: string) => apiFetch<Gate1State>(`${rev(r)}/gate1`),
  /** Replaces all building parts. */
  putParts: (r: string, parts: Omit<BuildingPart, "id">[]) =>
    apiFetch<BuildingPart[]>(`${rev(r)}/gate1/parts`, json("PUT", { parts })),
  /** Adds a space; manual_trace true for the fallback. */
  addSpace: (r: string, s: SpaceInput) => apiFetch<SpaceRow>(`${rev(r)}/gate1/spaces`, json("POST", s)),
  updateSpace: (r: string, id: string, s: SpaceInput) =>
    apiFetch<SpaceRow>(`${rev(r)}/gate1/spaces/${encodeURIComponent(id)}`, json("PUT", s)),
  addInput: (r: string, i: SystemInputInput) => apiFetch<SystemInputRow>(`${rev(r)}/gate1/inputs`, json("POST", i)),
  updateInput: (r: string, id: string, i: SystemInputInput) =>
    apiFetch<SystemInputRow>(`${rev(r)}/gate1/inputs/${encodeURIComponent(id)}`, json("PUT", i)),
  importSchedule: (r: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return apiFetch<ImportResponse>(`${rev(r)}/schedule/import`, { method: "POST", body: fd });
  },
  templateUrl: (edition: string) => `${BASE_URL}/templates/mep-system-schedule-${encodeURIComponent(edition)}.xlsx`,
  confirm: (r: string, rows: RowRef[]) => apiFetch<ConfirmResponse>(`${rev(r)}/gate1/confirm`, json("POST", { rows })),
  lineage: (r: string) => apiFetch<Lineage>(`${rev(r)}/lineage`),
  diff: (r: string) => apiFetch<RevisionDiff>(`${rev(r)}/diff`),
  confirmDiff: (r: string, hash: string) =>
    apiFetch<{ confirmed: boolean; hash: string }>(`${rev(r)}/diff/confirm`, json("POST", { hash })),
  results: (r: string) => apiFetch<RevisionResults>(`${rev(r)}/results`),
  freeze: (r: string) => apiFetch<{ frozen: boolean }>(`${rev(r)}/freeze`, { method: "POST" }),
  worksheet: (r: string) => apiFetch<Worksheet>(`${rev(r)}/review`),
  decide: (r: string, resultId: string, decision: string, reason: string, sampleId?: string, failCategory?: string, failReference?: string) =>
    apiFetch<{ seq: number }>(`${rev(r)}/review/decisions`, json("POST", {
      result_id: resultId, decision, reason, sample_id: sampleId ?? null,
      fail_category: failCategory ?? null, fail_reference: failReference || null })),
  prepareBulk: (r: string) => apiFetch<{ sample_id: string }>(`${rev(r)}/review/bulk/prepare`, { method: "POST" }),
  bulkApprove: (r: string, sampleId: string) =>
    apiFetch<{ approved: number }>(`${rev(r)}/review/bulk/approve`, json("POST", { sample_id: sampleId })),
  sign: (r: string, gate: "gate2" | "gate3", registration?: string) =>
    apiFetch<{ signed: string }>(`${rev(r)}/sign/${gate}`, json("POST", { registration: registration ?? null })),
  pkg: (r: string) => apiFetch<Package>(`${rev(r)}/package`),
  /** The signed PDF, fetched with the session token (an <a href> cannot send it). */
  pdfBlob: async (r: string) => {
    const token = await getToken();
    const res = await fetch(`${BASE_URL}${rev(r)}/package.pdf`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new ApiError(res.status, null, `HTTP ${res.status}`);
    return res.blob();
  },
  shareLinks: (r: string) => apiFetch<ShareLinkView[]>(`${rev(r)}/share-links`),
  createShare: (r: string, days: number, label?: string) =>
    apiFetch<{ token: string; expires_at: string }>(`${rev(r)}/share-links`, json("POST", { days, label: label || null })),
  revokeShare: (r: string, id: string) => apiFetch<{ revoked: boolean }>(`${rev(r)}/share-links/${id}`, { method: "DELETE" }),
  /** The public certifier view. The link token comes from the URL fragment and is sent ONCE, in a POST body, to this site's own
   *  /share-api (a rewrite to the API), which answers with a short-lived HttpOnly session cookie. No token is ever in a request URL. */
  sharedExchange: async (token: string): Promise<void> => {
    const res = await fetch("/share-api/exchange", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }), credentials: "same-origin" });
    if (!res.ok) throw new ApiError(res.status, null, "This link is not valid or has expired.");
  },
  sharedPackage: async (): Promise<Package> => {
    const res = await fetch("/share-api/package", { credentials: "same-origin" });
    if (!res.ok) throw new ApiError(res.status, null, "This link is not valid, or this session has ended: open the link again.");
    return (await res.json()) as Package;
  },
  sharedPdfUrl: () => "/share-api/report.pdf",
  acknowledgeFail: (r: string, resultId: string, note: string) =>
    apiFetch<{ id: number }>(`${rev(r)}/review/acknowledge-fail`, json("POST", { result_id: resultId, note })),
  skills: () => apiFetch<SkillSummary[]>("/skills"),
  skillCard: (name: string) => apiFetch<SkillCard>(`/skills/${encodeURIComponent(name)}/card`),
  setSkillDefaults: (name: string, defaults: Record<string, unknown>) =>
    apiFetch<{ saved: boolean }>(`/skills/${encodeURIComponent(name)}/defaults`, json("PUT", { defaults })),
  skillShortcut: (name: string, text: string) => apiFetch<ShortcutResult>(`/skills/${encodeURIComponent(name)}/shortcut`, json("POST", { text })),
  skillMissing: (name: string, spec: unknown) =>
    apiFetch<{ missing: { field: string; question: string }[] }>(`/skills/${encodeURIComponent(name)}/missing`, json("POST", { spec })),
  runSkill: (r: string, name: string, spec: unknown) =>
    apiFetch<SkillRunSummary>(`${rev(r)}/skills/${encodeURIComponent(name)}/run`, json("POST", { spec })),
  skillRuns: (r: string) => apiFetch<SkillRunRow[]>(`${rev(r)}/skill-runs`),
  useSkillOutputAsModel: (r: string, runId: string, which: "ifc" | "dxf") =>
    apiFetch<UploadResponse>(`${rev(r)}/skill-runs/${encodeURIComponent(runId)}/use-as-model?which=${which}`, { method: "POST" }),
  /** A released file, fetched with the session token (an <a href> cannot send it). */
  artifactBlob: async (id: string): Promise<Blob> => {
    const token = await getToken();
    const res = await fetch(`${BASE_URL}/artifacts/${encodeURIComponent(id)}/download`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new ApiError(res.status, null, "That file is not available (only files whose checks passed can be downloaded).");
    return res.blob();
  },
  agentsStatus: () => apiFetch<{ configured: boolean; reason: string }>("/agents/status"),
  cardPreview: (r: string, skill: string, spec: Record<string, unknown>) =>
    apiFetch<CardPreview>(`${rev(r)}/skills/${skill}/card-preview`, json("POST", { spec })),
  confirmCard: (r: string, skill: string, spec: Record<string, unknown>, sha: string, noteId?: string) =>
    apiFetch<{ confirmed: boolean }>(`${rev(r)}/skills/${skill}/confirm-card`, json("POST", { spec, spec_sha256: sha, note_id: noteId })),
  visionJobs: (r: string) => apiFetch<VisionJob[]>(`${rev(r)}/vision-jobs`),
  adminOverview: () => apiFetch<AdminOverview>("/admin/overview"),
  adminInvite: (email: string, role: string) => apiFetch<{ invitation_id: string; email_sent: boolean }>("/admin/invitations", json("POST", { email, role })),
  adminRevokeInvite: (id: string) => apiFetch<{ revoked: boolean }>(`/admin/invitations/${id}`, { method: "DELETE" }),
  adminPatchUser: (id: string, patch: { role?: string; is_admin?: boolean; active?: boolean }) => apiFetch<{ updated: boolean }>(`/admin/users/${id}`, json("PUT", patch)),
  adminSaveFirm: (f: { name: string; signer_mode: string; sample_size: number; near_miss_default: number | null }) => apiFetch<{ saved: boolean }>("/admin/firm", json("PUT", f)),
  adminAddTemplate: (kind: string, name: string, file: File) => {
    const fd = new FormData();
    fd.set("kind", kind); fd.set("name", name); fd.set("file", file);
    return apiFetch<{ template_id: string }>("/admin/templates", { method: "POST", body: fd });
  },
  myInvitation: () => apiFetch<MyInvitation>("/invitations/mine"),
  acceptInvitation: () => apiFetch<{ joined: boolean }>("/invitations/accept", { method: "POST" }),
  askAgent: (r: string, agent: string, message: string) => apiFetch<AgentReply>(`${rev(r)}/agents/${agent}/message`, json("POST", { message })),
  agentNotes: (r: string, kind?: string) => apiFetch<AgentNote[]>(`${rev(r)}/agent-notes${kind ? `?kind=${kind}` : ""}`),
  resolveAgentNote: (r: string, id: string, status: "answered" | "dismissed") =>
    apiFetch<{ resolved: boolean }>(`${rev(r)}/agent-notes/${id}/resolve`, json("POST", { status })),
  addEvidenceSpace: (r: string, b: { source_sha256: string; entity_key: string; area_unit: string; void_unit?: string }) =>
    apiFetch<{ space_id: string }>(`${rev(r)}/evidence/spaces`, json("POST", b)),
  removeEvidenceSpace: (r: string, spaceId: string) => apiFetch<{ removed: boolean }>(`${rev(r)}/evidence/spaces/${spaceId}`, { method: "DELETE" }),
  evidence: (r: string, source?: string) => apiFetch<EvidenceResponse>(`${rev(r)}/evidence${source ? `?source=${source}` : ""}`),
  runRules: (r: string) => apiFetch<RunRulesResponse>(`${rev(r)}/run-rules`, { method: "POST" }),
};
