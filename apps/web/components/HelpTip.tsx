"use client";
import { useId, useState } from "react";
import { HELP, type HelpKey } from "@/lib/onboarding";

/** Inline help: a real button that shows or hides a described region. Works with keyboard and screen readers; nothing depends on hover. */
export function HelpTip({ topic, label }: { topic: HelpKey; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} data-testid={`help-${topic}`}>
        {open ? "Hide help" : (label ?? "Help")}
      </button>
      <span id={id} role="note" hidden={!open} style={{ display: open ? "block" : "none", background: "#f1f5f9", padding: 8, marginTop: 4 }}>{HELP[topic]}</span>
    </span>
  );
}
