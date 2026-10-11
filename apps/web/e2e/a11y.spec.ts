import { readFileSync } from "node:fs";
import path from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Phase 10.4: axe checks (WCAG 2.1 A and AA) on every main screen. Zero violations allowed.
type Scenario = { project: string; revision: string; designer_email: string };
const state = JSON.parse(readFileSync(path.resolve(__dirname, ".state.json"), "utf-8")) as {
  scenarios: Record<string, Scenario>;
};
const MAILPIT = process.env.MEP_MAILPIT_URL ?? "http://127.0.0.1:54324";
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"];

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

async function expectNoViolations(page: Page, name: string): Promise<void> {
  await expect(page.locator("main").first(), `${name}: every page has a main landmark`).toBeVisible();
  const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const summary = results.violations.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`);
  expect(summary, `${name}: axe violations`).toEqual([]);
}

test("login screen has no axe violations", async ({ page }) => {
  await page.goto("/login");
  await expectNoViolations(page, "login");
});

test("signed-in screens have no axe violations", async ({ page }) => {
  test.setTimeout(240_000);
  const s = state.scenarios["mixed"];
  const rev = `/projects/${s.project}/revisions/${s.revision}`;
  await signIn(page, s.designer_email);
  const screens: [string, string][] = [
    ["projects", "/projects"],
    ["project history", `/projects/${s.project}`],
    ["gate1", `${rev}/gate1`],
    ["revision", `${rev}/diff`],
    ["review", `${rev}/review`],
    ["drafting", `${rev}/drafting`],
    ["services", `${rev}/services`],
    ["performance", `${rev}/performance`],
    ["preview", `${rev}/preview`],
    ["admin", "/admin"],
    ["billing", "/billing"],
    ["notifications", "/notifications"],
    ["standards", "/standards"],
    ["help", "/help"],
  ];
  for (const [name, url] of screens) {
    await page.goto(url);
    await page.waitForLoadState("networkidle");
    await expectNoViolations(page, name);
  }
});

test("share screen has no axe violations", async ({ page }) => {
  await page.goto("/share"); // no link fragment: the error state is the page a stranger sees first
  await page.waitForLoadState("networkidle");
  await expectNoViolations(page, "share");
});

test("Gate 1, review and sign-off controls are keyboard operable", async ({ page }) => {
  const s = state.scenarios["mixed"];
  await signIn(page, s.designer_email);
  for (const sub of ["gate1", "review"]) {
    await page.goto(`/projects/${s.project}/revisions/${s.revision}/${sub}`);
    await page.waitForLoadState("networkidle");
    const clickableNonControls = await page.locator("main [onclick]").count();
    expect(clickableNonControls, `${sub}: no div/span click handlers`).toBe(0);
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.tagName ?? "");
    expect(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY"]).toContain(focused);
  }
});
