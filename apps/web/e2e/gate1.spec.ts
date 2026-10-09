import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";

type Scenario = {
  project: string; revision: string; designer: string; checker: string; token: string;
  designer_email: string; checker_email: string; approver_email?: string; health_score: number | null; spaces: number;
};
const state = JSON.parse(readFileSync(path.resolve(__dirname, ".state.json"), "utf-8")) as {
  scenarios: Record<string, Scenario>; workbook: string; ifc: string; inputs_per_system: number; edition: string; rev_b: string;
};
const API = "http://127.0.0.1:8100";
const MAILPIT = process.env.MEP_MAILPIT_URL ?? "http://127.0.0.1:54324";

/** The one-time link Supabase Auth mailed to this address (local Mailpit catches every mail). */
async function magicLink(email: string): Promise<string> {
  for (let i = 0; i < 40; i++) {
    const found = await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:"${email}"`)}`)).json() as
      { messages?: { ID: string }[] };
    if (found.messages?.length) {
      const msg = await (await fetch(`${MAILPIT}/api/v1/message/${found.messages[0].ID}`)).json() as { HTML?: string; Text?: string };
      const body = `${msg.HTML ?? ""}\n${msg.Text ?? ""}`.replace(/&amp;/g, "&");
      const m = body.match(/https?:\/\/[^"'\s<>]+\/auth\/v1\/verify\?[^"'\s<>]+/);
      if (m) return m[0];
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no sign-in mail for ${email}`);
}

async function signIn(page: Page, email: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(email);
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  await expect(page.getByRole("status")).toContainText("Check your email");
  await page.goto(await magicLink(email));
  await expect(page.getByTestId("user-role")).toBeVisible();
}

async function openGate1(page: Page, s: Scenario): Promise<void> {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Revisions" })).toBeVisible();
  await page.getByTestId("revision-row").filter({ hasText: "1 E2E St" }).first().getByRole("link", { name: "Open Gate 1" }).click();
  await expect(page).toHaveURL(new RegExp(`/revisions/${s.revision}/gate1$`));
  await expect(page.getByRole("heading", { name: "Gate 1: confirm inputs" })).toBeVisible();
}

async function confirmAllUnconfirmed(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Select all unconfirmed" }).click();
  await page.getByRole("button", { name: /^Confirm selected/ }).click();
}

const CITATION = /NCC 2025 Volume One NCC2025, J6\S.* \((draft|approved)\)\t/;

test("sign in -> upload -> health score -> Gate 1 confirm -> cited DRAFT report", async ({ page }) => {
  const s = state.scenarios["flow"];
  await signIn(page, s.designer_email);
  await expect(page.getByTestId("user-role")).toHaveText("role: designer");
  await openGate1(page, s);

  // nothing ingested yet; upload the architect's IFC (its NAME is irrelevant, the server reads the content)
  await expect(page.getByRole("heading", { name: /no file ingested yet/ })).toBeVisible();
  await page.getByLabel("Upload IFC or DXF").setInputFiles({
    name: "architect-model.txt", mimeType: "text/plain", buffer: readFileSync(state.ifc),
  });
  await expect(page.getByTestId("upload-result")).toContainText("IFC: 2 space(s) read");
  await expect(page.getByRole("heading", { name: /^Ingest health: \d+%$/ })).toBeVisible();
  await expect(page.locator("[data-provenance='extracted']").first()).toBeVisible();   // read from the file, not confirmed
  await expect(page.getByRole("button", { name: "Run rules" })).toBeDisabled();

  // building parts by hand, then the schedule from the published Excel layout
  await page.getByRole("button", { name: "Add part" }).click();
  await page.getByLabel("Building class").selectOption("5");
  await page.getByLabel("Storeys").fill("3");
  await page.getByLabel("Part area").fill("400");
  await page.getByRole("button", { name: "Save parts" }).click();
  await expect(page.getByTestId("part-row")).toContainText("not confirmed");
  await page.getByLabel("Upload schedule").setInputFiles(state.workbook);
  await expect(page.getByTestId("input-row")).toHaveCount(state.inputs_per_system);

  // confirm project facts, the part, the spaces and every schedule row; then run
  await expect(page.getByTestId("project-facts-row")).toContainText("not confirmed");
  await confirmAllUnconfirmed(page);
  await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Run rules" })).toBeEnabled();
  await page.getByRole("button", { name: "Run rules" }).click();
  await expect(page.getByTestId("draft-banner")).toHaveText("DRAFT RULES: NOT ENGINEER-APPROVED");
  const rows = page.getByTestId("report-row");
  await expect(rows.first()).toBeVisible();
  expect(await rows.count()).toBeGreaterThan(0);
  for (const text of await rows.allInnerTexts()) expect(text).toMatch(CITATION);
});

test("protected routes send a signed-out visitor to the sign-in page, and sign-out closes the session", async ({ page }) => {
  const s = state.scenarios["flow"];
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.goto(`/projects/${s.project}/revisions/${s.revision}/gate1`);
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Gate 1: confirm inputs" })).toHaveCount(0);
  await signIn(page, s.designer_email);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
});

test("an unknown address gets no account and no link", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email address").fill("nobody-" + Date.now() + "@e2e.invalid");
  await page.getByRole("button", { name: "Send sign-in link" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText("Signups not allowed");
  await expect(page.getByRole("status")).toHaveCount(0);
});

test("a checker sees their role, cannot confirm, and the server refuses their upload", async ({ page }) => {
  const s = state.scenarios["refuse-space"];
  await signIn(page, s.checker_email);
  await expect(page.getByTestId("user-role")).toHaveText("role: checker");
  await openGate1(page, s);
  await expect(page.getByText("Only a designer confirms Gate 1 rows.")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Confirm selected/ })).toBeDisabled();
  await page.getByLabel("Upload IFC or DXF").setInputFiles({ name: "m.ifc", mimeType: "text/plain", buffer: readFileSync(state.ifc) });
  await expect(page.getByRole("alert").filter({ hasText: "forbidden" })).toBeVisible();
});

for (const kind of ["project", "building_part", "space", "system_input"] as const) {
  test(`one unconfirmed item (${kind}) blocks the run; confirming it releases the run`, async ({ page, request }) => {
    const s = state.scenarios[`refuse-${kind}`];
    await signIn(page, s.designer_email);
    await openGate1(page, s);
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

    await confirmAllUnconfirmed(page);
    await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Run rules" })).toBeEnabled();
    await page.getByRole("button", { name: "Run rules" }).click();
    await expect(page.getByTestId("draft-banner")).toBeVisible();
    await expect(page.getByTestId("report-row").first()).toBeVisible();
  });
}

test("a mixed-use project: each system names its building part, then the run reports per part", async ({ page }) => {
  const s = state.scenarios["mixed"];
  await signIn(page, s.designer_email);
  await openGate1(page, s);
  await expect(page.getByTestId("part-row")).toHaveCount(2);
  // without a part for every system the run is blocked
  await expect(page.getByRole("button", { name: "Run rules" })).toBeEnabled();      // everything that exists is confirmed
  await page.getByRole("button", { name: "Run rules" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "choose the building part" })).toBeVisible();

  await page.getByLabel("Building part for ahu-1").selectOption("0");
  await page.getByLabel("Building part for ahu-2").selectOption("1");
  await expect(page.getByTestId("input-row").filter({ hasText: "Part 2" })).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Run rules" })).toBeDisabled();       // the two assignments need confirming
  await confirmAllUnconfirmed(page);
  await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);
  await page.getByRole("button", { name: "Run rules" }).click();
  await expect(page.getByTestId("draft-banner")).toBeVisible();
  const text = (await page.getByTestId("report-row").allInnerTexts()).join("\n");
  expect(text).toContain("ahu-1");
  expect(text).toContain("ahu-2");
});

test("a new architect revision on a frozen one: diff, Gate 1 for what changed, confirm the diff, trace, re-run, freeze", async ({ page }) => {
  const s = state.scenarios["frozen"];
  await signIn(page, s.designer_email);
  await page.goto("/");
  const row = page.getByTestId("revision-row").filter({ hasText: "(frozen)" }).first();
  await expect(row).toContainText("frozen");
  await row.getByRole("link", { name: "Diff & results" }).click();
  await expect(page.getByRole("heading", { name: "Revision A (frozen)" })).toBeVisible();
  await expect(page.getByRole("table", { name: "" }).first()).toBeVisible();
  await expect(page.getByText("Results").first()).toBeVisible();

  // the architect's next model: it becomes a CHILD revision; the frozen one is untouched
  await page.getByLabel("Architect revision label").fill("B");
  await page.getByLabel("Upload new revision file").setInputFiles(state.rev_b);
  await expect(page.getByRole("heading", { name: "Revision B" })).toBeVisible();
  await expect(page).toHaveURL(/\/diff$/);
  await expect(page.getByTestId("space-change-changed")).toContainText("area_m2");
  await expect(page.getByTestId("space-change-added")).toContainText("Room 30");
  await expect(page.getByTestId("trace")).toContainText("-> ");
  await expect(page.locator("[data-stale='true']").first()).toContainText("STALE");

  // the diff cannot be confirmed (and rules cannot re-run) until the changed spaces are confirmed at Gate 1
  await expect(page.getByTestId("diff-pending")).toContainText("2 changed or added space(s)");
  await expect(page.getByRole("button", { name: "Confirm this diff" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Re-run rules" })).toBeDisabled();
  await page.getByRole("link", { name: "Open Gate 1" }).click();
  await expect(page.getByRole("heading", { name: "Gate 1: confirm inputs" })).toBeVisible();
  // a plan has no storeys: the designer states them for the two spaces that changed (the carried ones keep their confirmation)
  for (const i of [0, 3]) {
    const saved = page.waitForResponse((r) => r.request().method() === "PUT" && r.url().includes("/gate1/spaces/"));
    await page.getByLabel("storey", { exact: true }).nth(i).fill("Level 1");
    await page.getByLabel("storey", { exact: true }).nth(i).blur();
    expect((await saved).status()).toBe(200);
  }
  await confirmAllUnconfirmed(page);
  await expect(page.locator("[data-state='unconfirmed']")).toHaveCount(0);

  await page.goto(page.url().replace(/\/gate1$/, "/diff"));
  await expect(page.getByRole("button", { name: "Confirm this diff" })).toBeEnabled();
  await page.getByRole("button", { name: "Confirm this diff" }).click();
  await expect(page.getByRole("button", { name: "Diff confirmed" })).toBeDisabled();
  await page.getByRole("button", { name: "Re-run rules" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Run complete" })).toBeVisible();
  await expect(page.locator("[data-stale='true']")).toHaveCount(0);
  await page.getByRole("button", { name: "Freeze revision" }).click();
  await expect(page.getByRole("heading", { name: "Revision B (frozen)" })).toBeVisible();
});

test("designer froze it; checker reviews (spot-check, line by line) and signs Gate 2; approver signs Gate 3; signed package; certifier link", async ({ browser }) => {
  const s = state.scenarios["signoff"];
  const base = `/projects/${s.project}/revisions/${s.revision}/review`;
  async function asUser(email: string): Promise<Page> {
    const page = await (await browser.newContext()).newPage();
    await signIn(page, email);
    await page.goto(base);
    await expect(page.getByRole("heading", { name: /^Review and sign-off/ })).toBeVisible();
    return page;
  }

  // ---- the checker: Gate 2 -------------------------------------------------------------------------------------------
  const checker = await asUser(s.checker_email);
  await expect(checker.getByTestId("review-progress")).toContainText("Signed: Gate 1");           // the designer's freeze
  await checker.getByRole("button", { name: "Sign Gate 2 (checker)" }).click();
  await expect(checker.getByRole("alert").filter({ hasText: "stale, unclassified, unreviewed or not approved" })).toBeVisible();

  const draw = checker.getByRole("button", { name: "Draw the spot-check sample" });
  if (await draw.isEnabled()) {
    await draw.click();
    await expect(checker.getByTestId("sample-info")).toBeVisible();
    await checker.getByRole("button", { name: "Approve the remaining clean passes" }).click();     // refused: the sample is not examined
    await expect(checker.getByRole("alert").filter({ hasText: "have not been examined yet" })).toBeVisible();
    for (const line of await checker.locator("[data-sample='true']").all()) {
      await line.getByPlaceholder("reason (required)").fill("examined in the spot check against the clause");
      await line.getByRole("button", { name: "Approve", exact: true }).click();
      await expect(line).toHaveAttribute("data-decision", "approve");
    }
    await checker.getByRole("button", { name: "Approve the remaining clean passes" }).click();
    await expect(checker.getByRole("alert").filter({ hasText: "approved in bulk" })).toBeVisible();
  }
  // every other line, one by one with a reason
  for (let guard = 0; guard < 80; guard++) {
    const open = checker.locator("[data-testid='review-line'][data-decision='']");
    const n = await open.count();
    if (n === 0) break;
    await open.first().getByPlaceholder("reason (required)").fill("reviewed line by line against the cited clause");
    await open.first().getByRole("button", { name: "Approve", exact: true }).click();
    await expect(open).toHaveCount(n - 1);
  }
  await expect(checker.locator("[data-testid='review-line'][data-decision='']")).toHaveCount(0);
  await checker.getByRole("button", { name: "Sign Gate 2 (checker)" }).click();
  await expect(checker.getByRole("alert").filter({ hasText: "Gate 2 signed." })).toBeVisible();
  await expect(checker.getByTestId("package-status")).toContainText("NOT FULLY SIGNED: missing Gate 3");
  await expect(checker.getByRole("button", { name: "Create share link" })).toHaveCount(0);           // nothing to share yet

  // ---- the approver: Gate 3 with the registration number -------------------------------------------------------------
  const approver = await asUser(s.approver_email!);
  await approver.getByLabel("Registration number").fill("RPEQ 99999");
  await approver.getByRole("button", { name: "Sign Gate 3 (approver)" }).click();
  await expect(approver.getByRole("alert").filter({ hasText: "does not match" })).toBeVisible();
  await approver.getByLabel("Registration number").fill("RPEQ 12345");
  await approver.getByRole("button", { name: "Sign Gate 3 (approver)" }).click();
  await expect(approver.getByTestId("package-status")).toContainText("SIGNED: Gate 1 (designer), Gate 2 (checker) and Gate 3 (approver)");
  await expect(approver.getByTestId("signoffs")).toContainText("RPEQ 12345");
  await expect(approver.getByTestId("draft-banner")).toHaveText("DRAFT RULES: NOT ENGINEER-APPROVED");
  await expect(approver.getByTestId("ledger-status")).toContainText("verified");

  // ---- the certifier link ---------------------------------------------------------------------------------------------
  await approver.getByRole("button", { name: "Create share link" }).click();
  const url = (await approver.getByTestId("share-url").innerText()).trim();
  expect(url).toMatch(/\/share\/[A-Za-z0-9_-]{40,}$/);
  const certifier = await (await browser.newContext()).newPage();                                    // no sign-in at all
  await certifier.goto(url);
  await expect(certifier.getByRole("heading", { name: "Compliance package (read-only)" })).toBeVisible();
  await expect(certifier.getByTestId("package-status")).toContainText("SIGNED: Gate 1");
  await expect(certifier.getByTestId("package-results").getByRole("row")).not.toHaveCount(1);
  const pdf = await certifier.request.get((await certifier.getByTestId("shared-pdf").getAttribute("href")) as string);
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
  await approver.reload();
  await expect(approver.getByTestId("share-links")).toContainText("opened 2 time(s)");                // the page and the PDF were logged
  await approver.getByRole("button", { name: "Revoke" }).click();
  await expect(approver.getByTestId("share-links")).toContainText("REVOKED");
  await certifier.reload();
  await expect(certifier.getByRole("alert").filter({ hasText: "not valid or has expired" })).toBeVisible();
});
