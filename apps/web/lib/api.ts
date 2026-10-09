/** Typed API client. Every call goes through apiFetch. */
import type {
  ApiErrorBody, BuildingPart, ConfirmResponse, Gate1State, ImportResponse, RowRef,
  RunRulesResponse, SpaceInput, SpaceRow, SystemInputInput, SystemInputRow,
} from "./types";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Key under which the signed-in session's access token is kept (the sign-in screen is a later sprint). */
export const TOKEN_KEY = "mep_access_token";

/** The access token the API verifies. The role and firm are looked up server-side; nothing is read from the token here. */
export function getToken(): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked: the API will answer 401 and the screen shows it
  }
}

/** Error carrying the API's code and message verbatim. */
export class ApiError extends Error {
  constructor(public status: number, public code: string | null, message: string) {
    super(message);
  }
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
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
  runRules: (r: string) => apiFetch<RunRulesResponse>(`${rev(r)}/run-rules`, { method: "POST" }),
};
