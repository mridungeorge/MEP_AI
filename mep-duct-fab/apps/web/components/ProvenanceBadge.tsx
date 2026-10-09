import type { Provenance } from "@/lib/types";

const STYLE: Record<Provenance, { label: string; bg: string; fg: string }> = {
  extracted: { label: "extracted: not confirmed", bg: "#fde68a", fg: "#78350f" },
  default: { label: "default", bg: "#e5e7eb", fg: "#374151" },
  engineer_confirmed: { label: "engineer confirmed", bg: "#bbf7d0", fg: "#14532d" },
};

export function ProvenanceBadge({ provenance }: { provenance: Provenance }) {
  const s = STYLE[provenance];
  return (
    <span
      data-provenance={provenance}
      style={{ background: s.bg, color: s.fg, borderRadius: 4, padding: "1px 6px", fontSize: 12, whiteSpace: "nowrap" }}
    >
      {s.label}
    </span>
  );
}
