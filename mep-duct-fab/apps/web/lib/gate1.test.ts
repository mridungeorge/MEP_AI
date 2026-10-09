import { describe, expect, it } from "vitest";
import { applyEdit, canConfirm, canRun, hasValue, rowState, validatePart } from "./gate1";
import type { SpaceRow, SystemInputRow } from "./types";

const space = (o: Partial<SpaceRow> = {}): SpaceRow => ({
  id: "s1", ifc_guid: "g", name: "Office", area_m2: 20, use: "office", storey: 1,
  ceiling_void_mm: 300, provenance: "extracted", manual_trace: false, ...o,
});
const input = (o: Partial<SystemInputRow> = {}): SystemInputRow => ({
  id: "i1", name: "Supply air", value: 100, unit: "L/s", provenance: "default", ...o,
});

describe("row state", () => {
  it("only engineer_confirmed is confirmed", () => {
    expect(rowState(space())).toBe("unconfirmed");
    expect(rowState(space({ provenance: "default" }))).toBe("unconfirmed");
    expect(rowState(space({ provenance: "engineer_confirmed" }))).toBe("confirmed");
  });
  it("edit resets confirmation to default", () => {
    const e = applyEdit(space({ provenance: "engineer_confirmed" }), { area_m2: 25 });
    expect(e.provenance).toBe("default");
    expect(e.area_m2).toBe(25);
    expect(rowState(e)).toBe("unconfirmed");
  });
});

describe("canConfirm", () => {
  it("is false for empty selection", () => expect(canConfirm([space()], [], [])).toBe(false));
  it("is false when a selected row lacks a value", () => {
    const s = space({ area_m2: null });
    expect(hasValue(s, "space")).toBe(false);
    expect(canConfirm([s], [], [{ kind: "space", id: "s1" }])).toBe(false);
  });
  it("is true when all selected have values", () =>
    expect(
      canConfirm([space()], [input()], [{ kind: "space", id: "s1" }, { kind: "system_input", id: "i1" }]),
    ).toBe(true));
  it("is false for an unknown id", () => expect(canConfirm([], [], [{ kind: "space", id: "x" }])).toBe(false));
});

describe("canRun", () => {
  it("blocks with no rows", () => expect(canRun([], []).ok).toBe(false));
  it("blocks while any row is unconfirmed", () => {
    const r = canRun([space({ provenance: "engineer_confirmed" })], [input()]);
    expect(r.ok).toBe(false);
    expect(r.reason).toContain("1 row");
  });
  it("allows when all confirmed", () =>
    expect(
      canRun([space({ provenance: "engineer_confirmed" })], [input({ provenance: "engineer_confirmed" })]),
    ).toEqual({ ok: true, reason: null }));
});

describe("validatePart", () => {
  it("accepts valid", () => expect(validatePart({ building_class: "9b", storeys: 3, area_m2: 10 })).toBeNull());
  it("rejects bad values", () => {
    expect(validatePart({ building_class: "1" as never, storeys: 3, area_m2: 10 })).not.toBeNull();
    expect(validatePart({ building_class: "2", storeys: 201, area_m2: 10 })).not.toBeNull();
    expect(validatePart({ building_class: "2", storeys: 0, area_m2: 10 })).not.toBeNull();
    expect(validatePart({ building_class: "2", storeys: 1, area_m2: 0 })).not.toBeNull();
  });
});
