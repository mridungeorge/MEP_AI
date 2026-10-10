# Observability: errors, logs, health and uptime

## What is reported, and what never is
- **Errors (Sentry)** for the API (`SENTRY_DSN`), the drafting dispatcher (`SENTRY_DSN`, same variable on its host) and the web app (`NEXT_PUBLIC_SENTRY_DSN`).
  Off unless the variable is set. Before anything is sent the event is scrubbed (`mep.observability.scrub_event`, `apps/web/lib/sentry.ts`): no request body,
  headers, cookies, query string or URL fragment (the share-link token lives in the fragment), no user e-mail/IP, no e-mail addresses or token-shaped
  strings in messages, no local variables. Tests: `tests/api/test_observability.py`, `apps/web/lib/sentry.test.ts`.
- **Logs** are one JSON object per line on stdout (Railway keeps them): time, level, logger, message, and for requests `request_id`, method, path (no
  query), status, milliseconds. An exception is logged by type only. Every response carries `X-Request-Id`; quote it when reporting a problem.
- The skill containers have no network and report nothing; the dispatcher reports for them.

## Health checks
| Endpoint | Meaning | Use it for |
|---|---|---|
| `GET /healthz` | the process is up (never touches the database) | Railway's health check / restarts |
| `GET /readyz` | the database answers and the schema is present; 503 otherwise, with no detail | the uptime monitor and deploy verification |

## Uptime check (5 minutes to set up)
Use any external monitor (UptimeRobot free tier, Better Stack, Pingdom):
1. Monitor 1: `https://<api>/readyz`, every 1 minute, expect HTTP 200 and the text `"status":"ready"`; alert after 2 failures (e-mail + phone).
2. Monitor 2: `https://<web>/` expecting 200 (Vercel) every 5 minutes.
3. Keyword monitor on `https://<api>/healthz` is optional; Railway restarts the service itself.
4. Put the alert contact in the on-call note of the pilot agreement. A 503 on `/readyz` with `/healthz` fine means the database (Supabase) is the problem:
   check the Supabase status page, then the connection string (session pooler, port 5432).

## Setting it up
1. Create a Sentry project per service (or one project with the `service` tag, which the API/dispatcher set). Copy each DSN.
2. Railway variables: `SENTRY_DSN`, `MEP_ENV=staging`, optional `MEP_RELEASE=<git sha>`. Vercel: `NEXT_PUBLIC_SENTRY_DSN`, `NEXT_PUBLIC_MEP_ENV`.
3. Trigger a test error in staging (a request to an unknown revision id throws no error; use Sentry's own "send test event" from the project page) and check the event
   has no e-mail address or URL query in it.
