/** Pure helpers for the 3D preview: placed IFC geometry (as web-ifc streams it) to flat triangle buffers, and the box and camera maths. No WebGL and no WASM
 *  here, so it is unit-tested. IFC is Z-up in the model's length unit (web-ifc gives metres); the viewer is Y-up, so (x, y, z) becomes (x, z, -y). */

export interface PlacedPart {
  /** x y z nx ny nz per vertex, as web-ifc's GetVertexArray returns them */
  vertices: Float32Array;
  indices: Uint32Array;
  /** column-major 4x4 */
  transform: ArrayLike<number>;
  color: [number, number, number, number];
}

export interface Buffers {
  positions: Float32Array;
  normals: Float32Array;
  colors: Float32Array;
  triangles: number;
}

/** Limit the preview can hold: larger models are refused with a clear message, not left to hang the browser tab. */
export const MAX_TRIANGLES = 3_000_000;

export class TooLarge extends Error {}

export function triangleCount(parts: PlacedPart[]): number {
  let n = 0;
  for (const p of parts) n += Math.floor(p.indices.length / 3);
  return n;
}

/** The middle of a set of parts in IFC model coordinates (after placement), used as ONE shared origin so several models line up. */
export function partsCentre(parts: PlacedPart[]): [number, number, number] {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const p of parts) {
    const m = p.transform;
    for (let v = 0; v + 5 < p.vertices.length; v += 6) {
      const x = p.vertices[v], y = p.vertices[v + 1], z = p.vertices[v + 2];
      const w = [m[0] * x + m[4] * y + m[8] * z + m[12], m[1] * x + m[5] * y + m[9] * z + m[13], m[2] * x + m[6] * y + m[10] * z + m[14]];
      for (let k = 0; k < 3; k++) { if (w[k] < min[k]) min[k] = w[k]; if (w[k] > max[k]) max[k] = w[k]; }
    }
  }
  return min[0] === Infinity ? [0, 0, 0] : [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2];
}

export function mergeParts(parts: PlacedPart[], opacity = 1, origin: [number, number, number] = [0, 0, 0]): Buffers {
  const triCount = triangleCount(parts);
  if (triCount > MAX_TRIANGLES) throw new TooLarge(`This model has ${triCount.toLocaleString()} triangles; the preview shows up to ${MAX_TRIANGLES.toLocaleString()}.`);
  const positions = new Float32Array(triCount * 9);
  const normals = new Float32Array(triCount * 9);
  const colors = new Float32Array(triCount * 12);
  let t = 0;
  for (const p of parts) {
    const m = p.transform;
    for (let i = 0; i + 2 < p.indices.length; i += 3) {
      for (let k = 0; k < 3; k++) {
        const v = p.indices[i + k] * 6;
        const x = p.vertices[v], y = p.vertices[v + 1], z = p.vertices[v + 2];
        const nx = p.vertices[v + 3], ny = p.vertices[v + 4], nz = p.vertices[v + 5];
        // IFC (x, y, z) -> viewer (x, z, -y), after the placement transform
        const wx = m[0] * x + m[4] * y + m[8] * z + m[12] - origin[0];
        const wy = m[1] * x + m[5] * y + m[9] * z + m[13] - origin[1];
        const wz = m[2] * x + m[6] * y + m[10] * z + m[14] - origin[2];
        const vx = m[0] * nx + m[4] * ny + m[8] * nz;
        const vy = m[1] * nx + m[5] * ny + m[9] * nz;
        const vz = m[2] * nx + m[6] * ny + m[10] * nz;
        const len = Math.hypot(vx, vy, vz) || 1;
        const o = t * 9 + k * 3;
        positions[o] = wx; positions[o + 1] = wz; positions[o + 2] = 0 - wy;
        normals[o] = vx / len; normals[o + 1] = vz / len; normals[o + 2] = 0 - vy / len;
        const c = t * 12 + k * 4;
        colors[c] = p.color[0]; colors[c + 1] = p.color[1]; colors[c + 2] = p.color[2]; colors[c + 3] = p.color[3] * opacity;
      }
      t++;
    }
  }
  return { positions, normals, colors, triangles: t };
}

export interface Bounds { min: [number, number, number]; max: [number, number, number] }

export function boundsOf(positions: Float32Array): Bounds | null {
  if (positions.length < 3) return null;
  const min: [number, number, number] = [Infinity, Infinity, Infinity];
  const max: [number, number, number] = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i + 2 < positions.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      const v = positions[i + k];
      if (v < min[k]) min[k] = v;
      if (v > max[k]) max[k] = v;
    }
  }
  return { min, max };
}

export interface Camera { target: [number, number, number]; distance: number; yaw: number; pitch: number }

/** A camera that sees the whole box from a corner. */
export function fitCamera(b: Bounds): Camera {
  const target: [number, number, number] = [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
  const radius = Math.hypot(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]) / 2 || 1;
  return { target, distance: radius * 2.6, yaw: Math.PI / 4, pitch: Math.PI / 6 };
}

export function orbit(c: Camera, dx: number, dy: number): Camera {
  const limit = Math.PI / 2 - 0.05;
  return { ...c, yaw: c.yaw - dx * 0.01, pitch: Math.max(-limit, Math.min(limit, c.pitch + dy * 0.01)) };
}

export function zoom(c: Camera, wheel: number): Camera {
  return { ...c, distance: Math.max(0.05, c.distance * Math.exp(wheel * 0.001)) };
}

export function eyeOf(c: Camera): [number, number, number] {
  const cp = Math.cos(c.pitch);
  return [c.target[0] + c.distance * cp * Math.sin(c.yaw), c.target[1] + c.distance * Math.sin(c.pitch), c.target[2] + c.distance * cp * Math.cos(c.yaw)];
}

/** Column-major view-projection matrix for the camera (perspective, 45 degrees). */
export function viewProjection(c: Camera, aspect: number, near = 0.05, far = 5000): Float32Array {
  const eye = eyeOf(c);
  const f = norm([c.target[0] - eye[0], c.target[1] - eye[1], c.target[2] - eye[2]]);
  const s = norm(cross(f, [0, 1, 0]));
  const u = cross(s, f);
  const view = [s[0], u[0], -f[0], 0, s[1], u[1], -f[1], 0, s[2], u[2], -f[2], 0, -dot(s, eye), -dot(u, eye), dot(f, eye), 1];
  const t = 1 / Math.tan(Math.PI / 8);
  const proj = [t / aspect, 0, 0, 0, 0, t, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, (2 * far * near) / (near - far), 0];
  const out = new Float32Array(16);
  for (let col = 0; col < 4; col++) for (let row = 0; row < 4; row++) {
    let sum = 0;
    for (let k = 0; k < 4; k++) sum += proj[k * 4 + row] * view[col * 4 + k];
    out[col * 4 + row] = sum;
  }
  return out;
}

const dot = (a: number[], b: number[]) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: number[], b: number[]): [number, number, number] => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (a: number[]): [number, number, number] => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};
