"use client";
import { useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { NswDeclaration } from "@/lib/types";

/** The NSW design compliance declaration DRAFT: facts prefilled from the signed package, nothing declared, nothing lodged. */
export function DeclarationPanel({ revisionId }: { revisionId: string }) {
  const [d, setD] = useState<NswDeclaration | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const fail = (e: unknown) => setMessage(e instanceof ApiError ? e.message : String(e));
  return (
    <section aria-label="NSW design compliance declaration">
      <h2>NSW design compliance declaration (draft)</h2>
      <p>For NSW class 2, 3 and 9c projects, once all three gates are signed. The registered design practitioner reviews it, completes it and lodges it on the NSW Planning Portal: this app lodges nothing.</p>
      <button type="button" onClick={() => { setMessage(null); void api.nswDeclaration(revisionId).then(setD).catch(fail); }}>Prepare the draft</button>
      {message && <p role="alert">{message}</p>}
      {d && (
        <div>
          <p role="status" data-testid="declaration-banner" style={{ background: "#fee2e2", color: "#7f1d1d", padding: 8, fontWeight: 700 }}>{d.banner}</p>
          {d.rules_banner && <p role="status" style={{ background: "#fef3c7", padding: 8, fontWeight: 700 }}>{d.rules_banner}</p>}
          {d.independence_notice && <p role="status" style={{ background: "#fee2e2", padding: 8, fontWeight: 700 }}>{d.independence_notice}</p>}
          <p>{d.building.address}: NSW, class {d.applies_to.classes.join(", ")}, {d.building.ncc_edition}.</p>
          {d.applies_to.basis && <p><em>{d.applies_to.basis}</em></p>}
          <ul>{d.signed_by.map((s) => <li key={s.gate}>{s.gate}: {s.role} {s.email ?? ""} {s.registration_no ? `(${s.registration_no})` : ""}</li>)}</ul>
          <p>Accepted FAILs: {d.accepted_fails.length}. Performance solutions recorded: {d.performance_solutions.length}.</p>
          <p>The practitioner must complete:</p>
          <ul>{d.fields_to_complete.map((f) => <li key={f.field}><strong>{f.field}</strong>: {f.note}</li>)}</ul>
          <button type="button" onClick={() => { void api.nswDeclarationPdf(revisionId).then((b) => window.open(URL.createObjectURL(b), "_blank")).catch(fail); }}>Open the draft PDF</button>
        </div>
      )}
    </section>
  );
}
