import { describe, expect, it } from "vitest";
import { formatPoints, getPath, parsePoints, prune, setPath } from "./specpath";

describe("spec paths", () => {
  it("reads nested and indexed paths", () => {
    const spec = { seam: { type: "welded" }, rooms: [{ name: "A", outline: { width_mm: 5 } }] };
    expect(getPath(spec, "seam.type")).toBe("welded");
    expect(getPath(spec, "rooms[0].outline.width_mm")).toBe(5);
    expect(getPath(spec, "rooms[3].name")).toBeUndefined();
    expect(getPath(spec, "nope.deeper")).toBeUndefined();
  });
  it("writes without touching the original", () => {
    const spec = { rooms: [{ name: "A" }] };
    const next = setPath(spec, "rooms[0].height_mm", 2700);
    expect(next).toEqual({ rooms: [{ name: "A", height_mm: 2700 }] });
    expect(spec).toEqual({ rooms: [{ name: "A" }] });
    expect(setPath({}, "a.b[1].c", 1)).toEqual({ a: { b: [undefined, { c: 1 }] } });
  });
  it("prunes blanks so a missing value is sent as missing", () => {
    expect(prune({ a: "", b: Number.NaN, c: { d: "" }, e: [], f: [{ g: "" }], h: 0, i: false, j: "x" })).toEqual({ h: 0, i: false, j: "x" });
    expect(prune({ rooms: [{ name: "A", use: "" }] })).toEqual({ rooms: [{ name: "A" }] });
  });
  it("parses polygon corner lines", () => {
    expect(parsePoints("0, 0\n6000,0 \n6000 4000\n")).toEqual([[0, 0], [6000, 0], [6000, 4000]]);
    expect(parsePoints("0, 0\nfoo")).toBeNull();
    expect(formatPoints([[0, 0], [1, 2]])).toBe("0, 0\n1, 2");
    expect(formatPoints(undefined)).toBe("");
  });
});
