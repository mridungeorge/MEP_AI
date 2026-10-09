import type { IngestHealth } from "@/lib/types";

/** Displays the API's ingest health verbatim; the below-threshold decision is the API's. */
export function HealthPanel({ health }: { health: IngestHealth }) {
  return (
    <section aria-label="Ingest health">
      <h2>Ingest health: {health.score_percent}%</h2>
      {health.below_threshold && (
        <p role="alert" style={{ background: "#fee2e2", padding: 8 }}>
          Health is below the {health.threshold_percent}% threshold. Consider uploading a clean IFC or DXF, or use the
          manual trace fallback below.
        </p>
      )}
      {health.fixes.length > 0 && (
        <ul>
          {health.fixes.map((f, i) => (
            <li key={i}>{f}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
