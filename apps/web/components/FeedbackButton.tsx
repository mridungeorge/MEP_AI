"use client";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";

/** A small feedback form for signed-in members; administrators read the messages in the Admin screen. */
export function FeedbackButton() {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("idea");
  const [text, setText] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  if (!open) return <button type="button" onClick={() => setOpen(true)}>Send feedback</button>;
  return (
    <form aria-label="Feedback" onSubmit={(e) => {
      e.preventDefault();
      setMessage(null);
      void api.sendFeedback(kind, window.location.pathname, text).then(() => { setMessage("Thank you."); setText(""); }).catch((err) => setMessage(err instanceof ApiError ? err.message : "Could not send."));
    }}>
      <label>Kind{" "}<select value={kind} onChange={(e) => setKind(e.target.value)}><option value="bug">Something is wrong</option><option value="idea">An idea</option><option value="question">A question</option><option value="other">Other</option></select></label>{" "}
      <label>Message{" "}<textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} rows={3} cols={40} /></label>{" "}
      <button type="submit" disabled={text.trim().length < 3}>Send</button>{" "}<button type="button" onClick={() => setOpen(false)}>Close</button>
      {message && <span role="status"> {message}</span>}
    </form>
  );
}
