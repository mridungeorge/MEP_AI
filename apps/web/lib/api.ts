/** Typed API client. Every call goes through apiFetch. */
import { supabase } from "./supabase";
import type {
  ApiErrorBody, BuildingPart, ConfirmResponse, Gate1State, ImportResponse, RowRef,
  Lineage, Me, Package, ShareLinkView, Worksheet, RevisionDiff, RevisionResults, RevisionSummary, RunRulesResponse, SpaceInput, SpaceRow, SystemInputInput, SystemInputRow, UploadResponse,
} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** The access token of the signed-in session (the API verifies it and looks the role up itself; nothing is read from it here). */
export async function getToken(): Promise<string | null> {
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? null;
}

/** Error carrying the API's code and message verbatim. */
export class ApiError extends Error {
  constructor(public status: number, public code: string | null, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = await getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
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
  decide: (r: string, resultId: string, decision: string, reason: string, sampleId?: string) =>
    apiFetch<{ seq: number }>(`${rev(r)}/review/decisions`, json("POST", { result_id: resultId, decision, reason, sample_id: sampleId ?? null })),
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
  /** The public, read-only certifier view: no sign-in, the token is the credential. */
  shared: async (token: string): Promise<Package> => {
    const res = await fetch(`${BASE_URL}/share/${encodeURIComponent(token)}`);
    if (!res.ok) throw new ApiError(res.status, null, "This link is not valid or has expired.");
    return (await res.json()) as Package;
  },
  sharedPdfUrl: (token: string) => `${BASE_URL}/share/${encodeURIComponent(token)}/report.pdf`,
  runRules: (r: string) => apiFetch<RunRulesResponse>(`${rev(r)}/run-rules`, { method: "POST" }),
};
