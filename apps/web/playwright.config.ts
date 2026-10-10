import { defineConfig } from "@playwright/test";

// End-to-end: a real Next.js build, the real API (uvicorn) on the local Supabase Postgres, a real browser.
// Needs `supabase db reset` first (scripts/ci.sh does it). Servers and seed data are started/created here.
const API = "http://127.0.0.1:8100";
const WEB = "http://127.0.0.1:3100";
const DB_URL = process.env.MEP_TEST_DB_URL ?? "postgresql://postgres:postgres@127.0.0.1:54322/postgres";
const SECRET = process.env.MEP_JWT_SECRET ?? "super-secret-jwt-token-with-at-least-32-characters-long";
// the local Supabase (Auth, Storage): the public demo anon key for the demo secret, never a secret
const SUPABASE_URL = process.env.MEP_SUPABASE_URL ?? "http://127.0.0.1:54321";
const ANON_KEY = process.env.MEP_SUPABASE_ANON_KEY ??
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9.CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  workers: 1, // the scenarios share one database; each has its own firm, but keep the run readable
  retries: 0,
  reporter: [["list"]],
  globalSetup: "./e2e/global-setup.ts",
  use: { baseURL: WEB, headless: true, trace: "retain-on-failure" },
  webServer: [
    {
      command: "uv run uvicorn --factory mep.api.server:app_from_env --host 127.0.0.1 --port 8100",
      cwd: "../..",
      url: `${API}/openapi.json`,
      timeout: 60_000,
      reuseExistingServer: false,
      env: { ...(process.env as Record<string, string>), MEP_DB_URL: DB_URL, MEP_JWT_SECRET: SECRET, MEP_CORS_ORIGINS: WEB, MEP_ALLOW_DEMO_JWT_SECRET: "1", MEP_ALLOW_LOCAL_SKILLS: "1", MEP_COOKIE_SECURE: "0",
        MEP_SUPABASE_URL: SUPABASE_URL, MEP_SUPABASE_ANON_KEY: ANON_KEY },
    },
    {
      command: "pnpm exec next build && pnpm exec next start -H 127.0.0.1 -p 3100",
      url: `${WEB}/projects/ready/revisions/ready/gate1`, // "/" is a 404 (no root page); Playwright needs a 2xx
      timeout: 300_000,
      reuseExistingServer: false,
      env: { ...(process.env as Record<string, string>), NEXT_PUBLIC_API_URL: API,
        NEXT_PUBLIC_SUPABASE_URL: SUPABASE_URL, NEXT_PUBLIC_SUPABASE_ANON_KEY: ANON_KEY },
    },
  ],
});
