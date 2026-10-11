import { describe, expect, it } from "vitest";
import { MAX_TRIANGLES, TooLarge, boundsOf, eyeOf, fitCamera, mergeParts, orbit, partsCentre, triangleCount, viewProjection, zoom } from "./ifcmesh";

const identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/** one triangle in the IFC XY plane, normal +Z */
const triangle = (transform: number[] = identity) => ({
  vertices: new Float32Array([0, 0, 0, 0, 0, 1, 2, 0, 0, 0, 0, 1, 0, 3, 0, 0, 0, 1]),
  indices: new Uint32Array([0, 1, 2]),
  transform,
  color: [0.5, 0.25, 1, 1] as [number, number, number, number],
});

describe("mergeParts", () => {
  it("turns IFC Z-up into the viewer's Y-up", () => {
    const b = mergeParts([triangle()]);
    expect(b.triangles).toBe(1);
    expect(Array.from(b.positions)).toEqual([0, 0, 0, 2, 0, 0, 0, 0, -3]);
    expect(Array.from(b.normals).slice(0, 3)).toEqual([0, 1, 0]);
  });

  it("applies the placement transform (a translation of 10 in IFC x and 4 in z)", () => {
    const m = [...identity];
    m[12] = 10; m[14] = 4;
    const b = mergeParts([triangle(m)]);
    expect(b.positions[0]).toBe(10);
    expect(b.positions[1]).toBe(4);
  });

  it("carries the colour and scales alpha by the opacity", () => {
    const b = mergeParts([triangle()], 0.4);
    expect(b.colors[0]).toBe(0.5);
    expect(b.colors[3]).toBeCloseTo(0.4);
  });

  it("refuses a model above the triangle limit instead of hanging", () => {
    const indices = new Uint32Array((MAX_TRIANGLES + 1) * 3);
    expect(() => mergeParts([{ ...triangle(), indices }])).toThrow(TooLarge);
  });

  it("ignores a trailing partial triangle", () => {
    const p = triangle();
    expect(mergeParts([{ ...p, indices: new Uint32Array([0, 1, 2, 0]) }]).triangles).toBe(1);
  });
});

describe("camera", () => {
  it("bounds and fits a camera that looks at the middle from a distance", () => {
    const b = boundsOf(new Float32Array([0, 0, 0, 10, 4, 2]))!;
    expect(b.min).toEqual([0, 0, 0]);
    const cam = fitCamera(b);
    expect(cam.target).toEqual([5, 2, 1]);
    expect(cam.distance).toBeGreaterThan(Math.hypot(10, 4, 2) / 2);
    expect(boundsOf(new Float32Array([]))).toBeNull();
  });

  it("orbit keeps the pitch away from the poles and zoom stays positive", () => {
    let c = fitCamera({ min: [0, 0, 0], max: [1, 1, 1] });
    c = orbit(c, 0, 100000);
    expect(c.pitch).toBeLessThan(Math.PI / 2);
    c = zoom(c, -1e9);
    expect(c.distance).toBeGreaterThan(0);
    const eye = eyeOf(fitCamera({ min: [0, 0, 0], max: [2, 2, 2] }));
    expect(eye.every(Number.isFinite)).toBe(true);
  });

  it("projects the target to the middle of the screen", () => {
    const cam = fitCamera({ min: [-1, -1, -1], max: [1, 1, 1] });
    const m = viewProjection(cam, 1.5);
    const [x, y, z, w] = [0, 1, 2, 3].map((r) => m[12 + r]); // column 3 is the translation: transform of (0,0,0,1)
    expect(Math.abs(x / w)).toBeLessThan(1e-4);
    expect(Math.abs(y / w)).toBeLessThan(1e-4);
    expect(z / w).toBeGreaterThan(-1);
    expect(z / w).toBeLessThan(1);
  });
});

describe("shared origin", () => {
  it("puts two models on the same origin so they line up", () => {
    const far = (dx: number) => { const m = [...identity]; m[12] = 500000 + dx; return triangle(m); };
    const origin = partsCentre([far(0)]);
    const a = mergeParts([far(0)], 1, origin);
    const b = mergeParts([far(10)], 1, origin);
    expect(Math.abs(a.positions[0])).toBeLessThan(5);
    expect(b.positions[0] - a.positions[0]).toBeCloseTo(10);
  });
  it("counts triangles across parts", () => {
    expect(triangleCount([triangle(), triangle()])).toBe(2);
  });
});
