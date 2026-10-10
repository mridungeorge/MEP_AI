/** Browser error reporting, off unless NEXT_PUBLIC_SENTRY_DSN is set. Nothing personal leaves the browser: no user, no request data, no URLs with queries or fragments
 *  (the share-link token lives in the fragment), no e-mail addresses or token-shaped strings in messages. */
import * as Sentry from "@sentry/browser";

const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;
const SECRET = /\b(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*|[A-Fa-f0-9]{40,}|sk_[A-Za-z0-9_]{10,}|re_[A-Za-z0-9_]{10,})\b/g;

export function scrubString(s: string): string {
  return s.replace(EMAIL, "[redacted]").replace(SECRET, "[redacted]");
}

function stripUrl(u: string): string {
  return u.split("#")[0].split("?")[0];
}

export function scrubEvent<T extends Sentry.ErrorEvent>(event: T): T {
  delete event.user;
  delete event.server_name;
  if (event.request) {
    event.request = { url: event.request.url ? stripUrl(event.request.url) : undefined };
  }
  if (event.message) event.message = scrubString(event.message);
  for (const ex of event.exception?.values ?? []) {
    if (ex.value) ex.value = scrubString(ex.value);
    for (const f of ex.stacktrace?.frames ?? []) delete f.vars;
  }
  event.breadcrumbs = (event.breadcrumbs ?? []).map((b) => ({
    ...b,
    message: b.message ? scrubString(b.message) : b.message,
    data: undefined,
  }));
  return event;
}

let started = false;
export function initSentry(): boolean {
  const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
  if (!dsn || started || typeof window === "undefined") return false;
  Sentry.init({ dsn, tracesSampleRate: 0, beforeSend: scrubEvent, environment: process.env.NEXT_PUBLIC_MEP_ENV ?? "development" });
  started = true;
  return true;
}
