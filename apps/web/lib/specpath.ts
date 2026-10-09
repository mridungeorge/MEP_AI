/** Dotted/indexed paths into a spec card ("seam.type", "rooms[2].outline.width_mm"): read, write without mutating, and tidy before sending. */

type Json = unknown;
type Step = string | number;

function steps(path: string): Step[] {
  const out: Step[] = [];
  for (const m of path.matchAll(/([^.[\]]+)|\[(\d+)\]/g)) out.push(m[2] !== undefined ? Number(m[2]) : (m[1] as string));
  return out;
}

export function getPath(obj: Json, path: string): Json {
  let node: Json = obj;
  for (const s of steps(path)) {
    if (node === null || typeof node !== "object") return undefined;
    node = (node as Record<string | number, Json>)[s];
  }
  return node;
}

/** A copy of `obj` with `path` set to `value` (objects and arrays are created as needed). */
export function setPath<T>(obj: T, path: string, value: Json): T {
  const list = steps(path);
  const rec = (node: Json, i: number): Json => {
    const key = list[i];
    const base: Record<string | number, Json> | Json[] =
      node !== null && typeof node === "object" ? (Array.isArray(node) ? [...node] : { ...(node as object) }) : typeof key === "number" ? [] : {};
    if (i === list.length - 1) (base as Record<string | number, Json>)[key] = value;
    else (base as Record<string | number, Json>)[key] = rec((base as Record<string | number, Json>)[key], i + 1);
    return base;
  };
  return rec(obj, 0) as T;
}

/** Remove what the designer left blank so the server sees "not given" rather than "": empty strings, NaN, empty objects and arrays. */
export function prune(value: Json): Json {
  if (Array.isArray(value)) {
    const items = value.map(prune).filter((v) => v !== undefined);
    return items.length ? items : undefined;
  }
  if (value !== null && typeof value === "object") {
    const out: Record<string, Json> = {};
    for (const [k, v] of Object.entries(value as Record<string, Json>)) {
      const p = prune(v);
      if (p !== undefined) out[k] = p;
    }
    return Object.keys(out).length ? out : undefined;
  }
  if (value === "" || value === null || (typeof value === "number" && Number.isNaN(value))) return undefined;
  return value;
}

/** "0, 0\n6000, 0\n6000, 4000" -> [[0,0],[6000,0],[6000,4000]]; null when a line is not two numbers. */
export function parsePoints(text: string): number[][] | null {
  const out: number[][] = [];
  for (const line of text.split(/\n/)) {
    if (!line.trim()) continue;
    const m = line.trim().match(/^(-?\d+(?:\.\d+)?)\s*[, ;]\s*(-?\d+(?:\.\d+)?)$/);
    if (!m) return null;
    out.push([Number(m[1]), Number(m[2])]);
  }
  return out;
}

export function formatPoints(points: unknown): string {
  return Array.isArray(points) ? points.map((p) => (Array.isArray(p) ? p.join(", ") : "")).join("\n") : "";
}
