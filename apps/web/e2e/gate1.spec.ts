import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

type Scenario = {
  project: string; revision: string; designer: string; checker: string; token: string;
  health_score: number | null; spaces: number;
};
const state = JSON.parse(readFileSync(path.resolve(__dirname, ".state.json"), "utf-8")) as {
  scenarios: Record<string, Scenario>; workbook: string; inputs_per_system: number; edition: string;
};
const API = "http://127.0.0.1:8100";

async function open(page: Page, s: Scenario): Promise<void> {
  await page.addInitScript((t) => window.localStorage.setItem("mep_access_token", t), s.token);
  await page.goto(`/projects/${s.project}/revisions/${s.revision}/gate1`);
  await expect(page.getByRole("heading", { name: "Gate 1: confirm inputs" })).toBeVisible();
}

async function confirmAllUnconfirmed(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Select all unconfirmed" }).click();
  await page.getByRole("button", { name: /^Confirm selected/ }).click();
}

test("fixture IFC -> health score -> confirm everything -> engine run -> cited DRAFT report", async ({ page }) => {
  const s = state.scenarios["flow"];
  await open(page, s);

  // the health score of the ingested IFC is the API's number, shown as is
  expect(s.health_score).not.toBeNull();
  await expect(page.getByRole("heading", { name: `Ingest health: ${s.health_score}%` })).toBeVisible();
  await expect(page.getByTestId("part-row")).toHaveCount(0);
  await expect(page.locator("[data-provenance='extracted']").first()).toBeVisible();   // IFC spaces: not confirmed

  // nothing runs yet
  await expect(page.getByRole("button", { name: "Run rules" })).toBeDisabled();
  await expect(page.getByTestId("run-blocked")).toContainText("not confirmed");

  // building parts by hand
  await page.getByRole("button", { name: "Add part" }).click();
  await page.getByLabel("Building class").selectOption("5");
  await page.getByLabel("Storeys").fill("3");
  await page.getByLabel("Part area").fill("400");
  await page.getByRole("button", { name: "Save parts" }).click();
  await expect(page.getByTestId("part-row")).toContainText("not confirmed");

  // the schedule from the published Excel layout: imported rows are 'extracted' until confirmed
  await page.getByLabel("Upload schedule").setInputFiles(state.workbook);
  await expect(page.getByTestId("input-row")).toHaveCount(state.inputs_per_system);
  await expect(page.getByTestId("input-row").first().locator("[data-provenance='extracted']")).toBeVisible();

  // confirm project facts, the part, every space and every schedule row
  await expect(page.getByTestId("project-facts-row")).toContainText("not confirmed");
  await confirmAllUnconfirmed(page);
  await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);
  await expect(page.getByTestId("project-facts-row")).toContainText("confirmed");
  await expect(page.getByTestId("project-facts-row")).not.toContainText("not confirmed");

  // run: the engine's cited report, marked DRAFT
  await expect(page.getByRole("button", { name: "Run rules" })).toBeEnabled();
  await page.getByRole("button", { name: "Run rules" }).click();
  await expect(page.getByTestId("draft-banner")).toHaveText("DRAFT RULES: NOT ENGINEER-APPROVED");
  const rows = page.getByTestId("report-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(0);
  for (const text of await rows.allInnerTexts()) {
    expect(text).toMatch(/NCC 2025 Volume One NCC2025, J6\S.* \((draft|approved)\)\t/);
  }
});

for (const kind of ["project", "building_part", "space", "system_input"] as const) {
  test(`one unconfirmed item (${kind}) blocks the run; confirming it releases the run`, async ({ page, request }) => {
    const s = state.scenarios[`refuse-${kind}`];
    await open(page, s);
    await expect(page.getByRole("button", { name: "Run rules" })).toBeDisabled();
    await expect(page.getByTestId("run-blocked")).toBeVisible();

    // the API refuses on its own account, not only the button. An IFC-extracted space that is not confirmed is
    // refused as extracted_inputs (rule 10); every other unconfirmed kind is gate1_required.
    const refused = await request.post(`${API}/revisions/${s.revision}/run-rules`, {
      headers: { Authorization: `Bearer ${s.token}` },
    });
    expect(refused.status()).toBe(409);
    const body = await refused.json();
    expect(body.code).toBe(kind === "space" ? "extracted_inputs" : "gate1_required");
    expect(body.reasons.length).toBe(1);

    // the signed-in user is a designer (the role comes from the database, not from the token)
    const view = await (await request.get(`${API}/revisions/${s.revision}/gate1`, {
      headers: { Authorization: `Bearer ${s.token}` } })).json();
    expect(view.role).toBe("designer");

    await confirmAllUnconfirmed(page);
    await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Run rules" })).toBeEnabled();
    await page.getByRole("button", { name: "Run rules" }).click();
    await expect(page.getByTestId("draft-banner")).toBeVisible();
    await expect(page.getByTestId("report-row").first()).toBeVisible();
  });
}
