# Security notes (Phase 10.1)

## Authentication and CSRF

- The API authenticates with a bearer token in the `Authorization` header (Supabase JWT, verified per request). Browsers never
  attach that header on their own, so a cross-site page cannot make an authenticated request: classic CSRF does not apply to these routes.
- The one cookie is the certifier share session, `mep_share` (`apps/api/mep/api/review.py`): `HttpOnly`, `SameSite=Strict`,
  `Secure` unless `MEP_COOKIE_SECURE=0` (local runs), `path=/`, short-lived, `Cache-Control: no-store`.
- State-changing request that relies on the cookie: only `POST /share/exchange`, which does not read the cookie; it needs the link token
  in the body (the token lives in the URL fragment, never sent to a server, so a third-party site cannot know it). It sets the cookie.
  Everything the cookie authorises (`/share/package`, `/share/report.pdf`, `/share/*` reads) is GET and read-only.
- Conclusion: `SameSite=Strict` already stops cross-site sends of the cookie, the cookie grants read-only access, and the one POST needs a
  secret. No code change was needed.
- CORS: the API should allow only the web origin; the web app reaches the share endpoints through its own same-origin rewrite (`/share-api`).

## Rate limiting

`apps/api/mep/ratelimit.py`: in-memory sliding window, thread-safe, bounded memory (LRU key eviction), pure ASGI middleware.
Bucket key is a SHA-256 of the raw bearer token when present (not verified there), else the rightmost `X-Forwarded-For` hop, else the peer.
Classes and defaults (per minute): upload 30, run 60, share 120, auth-ish (`/me`, `/invitations/*`) 120. Override with
`MEP_RATELIMIT_<UPLOAD|RUN|SHARE|AUTH>=<max>[/<seconds>]`; disable with `MEP_RATELIMIT=off`. Over the limit: HTTP 429, `Retry-After`,
`{"detail":{"code":"rate_limited","message":...}}`. Limits are per process: with several workers each counts alone, so use an
edge limiter as well in production. Because a forged bearer string gets its own bucket, per-user limits do not stop a client rotating
fake tokens; those requests still fail authentication, and the unverified bucket count is bounded by the LRU.
Install: `from mep import ratelimit` then `ratelimit.install(app)` in `server.py` after the app is created.

## Browser headers

`apps/web/next.config.mjs` sets CSP, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy` and HSTS on every route
(`/share` keeps `Referrer-Policy: no-referrer` and `no-store`). CSP: `default-src 'self'`; `connect-src` adds the API and Supabase origins
from `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_SUPABASE_URL`; `'wasm-unsafe-eval'` for web-ifc; `frame-ancestors 'none'`.
Known weakness: `script-src` and `style-src` include `'unsafe-inline'` because Next 14 injects inline bootstrap scripts; moving to
nonces needs middleware and dynamic rendering. Dev adds `'unsafe-eval'` and `ws:` only.

## Dependency and image scanning

`.github/workflows/security.yml`: `pip-audit` on the exported lock, `pnpm audit --audit-level high`, and a Trivy scan (HIGH, CRITICAL,
exit code 1) of the API, worker and web images. Suppressions live in `.trivyignore` and are justified in `docs/allowlist-audit.md`.
