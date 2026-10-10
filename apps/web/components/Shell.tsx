"use client";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api, getActingRole, setActingRole } from "@/lib/api";
import { initSentry } from "@/lib/sentry";
import { JoinFirm } from "./JoinFirm";
import { useSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";
import type { Me } from "@/lib/types";

const PUBLIC_PATHS = ["/login", "/terms", "/privacy"];

/** Header with who is signed in and their role (a display: the server enforces every permission itself). */
function Header({ email }: { email: string | undefined }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.me().then(setMe).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  return (
    <header style={{ display: "flex", gap: 16, alignItems: "center", borderBottom: "1px solid #ddd", paddingBottom: 8, marginBottom: 16 }}>
      <a href="/" style={{ fontWeight: 600, textDecoration: "none", color: "inherit" }}>MEP Co-pilot</a>
      <a href="/projects">Projects</a>
      <a href="/notifications">Notifications</a>
      {me?.is_admin && <a href="/admin">Admin</a>}
      {me?.is_admin && <a href="/billing">Billing</a>}
      {me?.platform_admin && <a href="/platform">Registrations</a>}
      <span style={{ flex: 1 }} />
      <span data-testid="user-email">{email}</span>
      {me && me.independence_notice && <strong style={{ color: "#7f1d1d" }}>{me.independence_notice}</strong>}
      {me && (me.available_roles?.length ?? 0) > 1 ? (
        <label>acting as{" "}
          <select aria-label="Acting role" value={getActingRole() ?? me.own_role ?? me.role}
                  onChange={(e) => { setActingRole(e.target.value === me.own_role ? null : e.target.value); window.location.reload(); }}>
            {me.available_roles!.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
      ) : null}
      {me && <span data-testid="user-role" title={me.firm_name ?? ""}>role: {me.role}</span>}
      {error && <span role="alert" style={{ color: "#b91c1c" }}>{error}</span>}
      <button type="button" onClick={() => { setActingRole(null); void supabase().auth.signOut(); }}>Sign out</button>
    </header>
  );
}

/** Signed-out visitors are sent to /login; nothing of a protected page renders (or loads data) before there is a session. */
export function Shell({ children }: { children: ReactNode }) {
  useEffect(() => { initSentry(); }, []);
  const { session, loading } = useSession();
  const path = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(path ?? "") || path === "/share" || (path ?? "").startsWith("/share/");
  useEffect(() => {
    if (!loading && !session && !isPublic) router.replace("/login");
  }, [loading, session, isPublic, router]);
  if (isPublic) return <>{children}</>;
  if (loading || !session) return <p>Checking your session…</p>;
  return <Gate email={session.user.email}>{children}</Gate>;
}

/** A signed-in person with no firm sees the join screen; everyone else gets the app. */
function Gate({ email, children }: { email: string | undefined; children: ReactNode }) {
  const [state, setState] = useState<"checking" | "member" | "nomember" | "deactivated">("checking");
  useEffect(() => {
    api.me().then(() => setState("member")).catch((e: unknown) => setState(e instanceof Error && /no such user/i.test(e.message) ? "nomember" : e instanceof Error && /deactivated/i.test(e.message) ? "deactivated" : "member"));
  }, []);
  if (state === "checking") return <p>Checking your account…</p>;
  if (state === "nomember") return <JoinFirm />;
  if (state === "deactivated") return <main><h1>Account deactivated</h1><p>Your firm&apos;s administrator has deactivated this account. Contact them if this is a mistake.</p></main>;
  return (<><Header email={email} />{children}</>);
}
