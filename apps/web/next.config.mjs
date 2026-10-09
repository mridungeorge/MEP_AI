import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `@/...` is declared in tsconfig `paths`, but Next 14 cannot read paths through TypeScript 7; alias it explicitly.
  webpack(config) {
    config.resolve.alias["@"] = here;
    return config;
  },
};
export default nextConfig;
