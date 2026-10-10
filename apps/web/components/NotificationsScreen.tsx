"use client";
import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { NotificationPrefs } from "@/lib/types";

const LABELS: Record<keyof NotificationPrefs["kinds"], string> = {
  review_requested: "A review is waiting for me",
  changes_requested: "A checker asked for changes to my work",
  signed: "A gate was signed on a revision I started or administer",
  share_link_opened: "A certifier link I made was opened",
};

/** Which e-mails this person gets. E-mails never contain results: only that something needs attention, and a link. */
export function NotificationsScreen() {
  const [kinds, setKinds] = useState<NotificationPrefs["kinds"] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  useEffect(() => { api.notificationPrefs().then((p) => setKinds(p.kinds)).catch((e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e))); }, []);
  if (!kinds) return <main><h1>Notifications</h1><p>{message ?? "Loading…"}</p></main>;
  return (
    <main>
      <h1>Notifications</h1>
      {(Object.keys(LABELS) as (keyof typeof LABELS)[]).map((k) => (
        <p key={k}><label><input type="checkbox" checked={kinds[k]} onChange={(e) => setKinds({ ...kinds, [k]: e.target.checked })} /> {LABELS[k]}</label></p>
      ))}
      <button type="button" onClick={() => void api.saveNotificationPrefs(kinds).then(() => setMessage("Saved.")).catch((e: unknown) => setMessage(String(e)))}>Save</button>
      {message && <p role="status">{message}</p>}
    </main>
  );
}
