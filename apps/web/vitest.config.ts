import { defineConfig } from "vitest/config";

// unit tests only: the Playwright specs in e2e/ run with `pnpm run e2e`
export default defineConfig({ test: { include: ["lib/**/*.test.ts"] } });
