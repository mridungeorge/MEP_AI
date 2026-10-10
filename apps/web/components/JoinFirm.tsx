"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { MyInvitation } from "@/lib/types";

/** Shown to a person who has signed in but belongs to no firm: they can join only through an invitation addressed to their confirmed e-mail. */
export function JoinFirm() {
  const [inv, setInv] = useState<MyInvitation | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { void api.myInvitation().then(setInv).catch((e: unknown) => setMessage(e instanceof Error ? e.message : String(e))); }, []);
  if (!inv) return <main><p>{message ?? "Checking for an invitation…"}</p></main>;
  return (
    <main>
      <h1>Join your firm</h1>
      {inv.invitation ? (
        <>
          <p>{inv.invitation.firm} has invited you as a {inv.invitation.role}.</p>
          <button type="button" onClick={() => void api.acceptInvitation().then(() => window.location.assign("/")).catch((e: unknown) =>
            setMessage(e instanceof ApiError ? e.message : String(e)))}>Join {inv.invitation.firm}</button>
        </>
      ) : <p>There is no open invitation for the address you signed in with. Ask your firm&apos;s administrator to invite that address.</p>}
      {message && <p role="alert">{message}</p>}
    </main>
  );
}
