import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const api = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const supabase = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "http://127.0.0.1:54321";
const origin = (u) => { try { return new URL(u).origin; } catch { return ""; } };
const isDev = process.env.NODE_ENV !== "production";

// Next injects inline bootstrap scripts, so script-src needs 'unsafe-inline' until nonces are wired; 'wasm-unsafe-eval' is for web-ifc (3D preview).
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src ${["'self'", origin(api), origin(supabase), ...(isDev ? ["ws:"] : [])].filter(Boolean).join(" ")}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const securityHeaders = [
  { key: "Content-Security-Policy", value: csp },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
];

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
    return [
      // /share keeps its own stricter Referrer-Policy (one value per header), so the general rule skips it.
      { source: "/((?!share$).*)", headers: securityHeaders },
      { source: "/share", headers: [...securityHeaders.filter((h) => h.key !== "Referrer-Policy"), { key: "Referrer-Policy", value: "no-referrer" }, { key: "Cache-Control", value: "no-store" }] },
    ];
  },
};
export default nextConfig;
