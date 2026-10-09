import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // `@/...` is declared in tsconfig `paths`, but Next 14 cannot read paths through TypeScript 7; alias it explicitly.
  webpack(config) {
    config.resolve.alias["@"] = here;
    return config;
  },
  // The certifier's pages talk to the API THROUGH this site (same origin), so the share-session cookie is first-party and the link token
  // never has to appear in any request URL: the token lives in the page's URL fragment and is POSTed once to /share-api/exchange.
  async rewrites() {
    return [{ source: "/share-api/:path*", destination: `${api}/share/:path*` }];
  },
  async headers() {
    return [{ source: "/share", headers: [{ key: "Referrer-Policy", value: "no-referrer" }, { key: "Cache-Control", value: "no-store" }] }];
  },
};
export default nextConfig;
