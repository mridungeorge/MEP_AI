"use client";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useSession } from "@/lib/session";
import { supabase } from "@/lib/supabase";
import type { Me } from "@/lib/types";

const PUBLIC_PATHS = ["/login"];

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
      <span style={{ flex: 1 }} />
      <span data-testid="user-email">{email}</span>
      {me && <span data-testid="user-role" title={me.firm_name ?? ""}>role: {me.role}</span>}
      {error && <span role="alert" style={{ color: "#b91c1c" }}>{error}</span>}
      <button type="button" onClick={() => void supabase().auth.signOut()}>Sign out</button>
    </header>
  );
}

/** Signed-out visitors are sent to /login; nothing of a protected page renders (or loads data) before there is a session. */
export function Shell({ children }: { children: ReactNode }) {
  const { session, loading } = useSession();
  const path = usePathname();
  const router = useRouter();
  const isPublic = PUBLIC_PATHS.includes(path ?? "");
  useEffect(() => {
    if (!loading && !session && !isPublic) router.replace("/login");
  }, [loading, session, isPublic, router]);
  if (isPublic) return <>{children}</>;
  if (loading || !session) return <p>Checking your session…</p>;
  return (
    <>
      <Header email={session.user.email} />
      {children}
    </>
  );
}
