import { execFileSync } from "node:child_process";
import path from "node:path";

/** Seeds one fresh firm/project/revision per scenario (see scripts/e2e_seed.py) and writes e2e/.state.json. */
export default function globalSetup(): void {
  const repo = path.resolve(__dirname, "../../..");
  const out = path.resolve(__dirname, ".state.json");
  execFileSync("uv", ["run", "python", "scripts/e2e_seed.py", "--out", out], {
    cwd: repo,
    stdio: "inherit",
    env: {
      ...process.env,
      MEP_JWT_SECRET: process.env.MEP_JWT_SECRET ?? "super-secret-jwt-token-with-at-least-32-characters-long",
    },
  });
}
