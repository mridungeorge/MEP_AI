import { describe, expect, it } from "vitest";
import { HELP, deriveSteps } from "./onboarding";
import type { ProjectRow } from "./types";

const row = (signed: string[], created = "2026-01-01"): ProjectRow => ({
  id: "p1", address: "1 A St", state: "VIC", ncc_edition: "NCC2025", climate_zone: 6, created_at: created, revisions: 1, status: "drafting",
  latest_revision: { id: "r1", architect_rev: "A", frozen: false, gates_signed: signed },
});

describe("deriveSteps", () => {
  it("starts at create when the firm has no projects", () => {
    const s = deriveSteps([]);
    expect(s.map((x) => x.status)).toEqual(["current", "todo", "todo", "todo", "todo"]);
    expect(s.every((x) => x.href === null)).toBe(true);
  });
  it("moves to upload once a project exists and links to Gate 1", () => {
    const s = deriveSteps([row([])]);
    expect(s.map((x) => x.status)).toEqual(["done", "current", "todo", "todo", "todo"]);
    expect(s[1]?.href).toBe("/projects/p1/revisions/r1/gate1");
  });
  it("treats upload, confirm and run as done after Gate 1 is signed", () => {
    const s = deriveSteps([row(["gate1"])]);
    expect(s.map((x) => x.status)).toEqual(["done", "done", "done", "done", "current"]);
    expect(s[4]?.href).toBe("/projects/p1/revisions/r1/review");
  });
  it("is all done after Gate 3", () => {
    expect(deriveSteps([row(["gate1", "gate2", "gate3"])]).every((x) => x.status === "done")).toBe(true);
  });
  it("uses the newest project", () => {
    const newer = { ...row([]), id: "p2", created_at: "2026-02-01" };
    expect(deriveSteps([row(["gate1"]), newer])[1]?.status).toBe("current");
  });
});

describe("HELP", () => {
  it("never claims compliance", () => {
    for (const t of Object.values(HELP)) expect(t).not.toMatch(/\bcomplies\b|\bis compliant\b/i);
  });
});
