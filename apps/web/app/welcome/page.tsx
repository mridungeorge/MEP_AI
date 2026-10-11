export default function WelcomePage() {
  return (
    <main>
      <h1>MEP Co-pilot</h1>
      <p role="note" data-testid="draft-copy" style={{ border: "2px solid #b45309", padding: 8, fontWeight: 700 }}>DRAFT COPY: wording on this page has not been reviewed.</p>
      <p>From the architect&apos;s model to a signed compliance package for mechanical services: inputs confirmed by an engineer, rules applied the same way every time, every change traced.</p>
      <ul>
        <li>Confirm what was read from the architect&apos;s model before anything is checked.</li>
        <li>Rules come from versioned rule files, never from a language model. Today every rule is a DRAFT and not engineer-approved.</li>
        <li>Three gates: designer, checker, approver, each recorded in a tamper-evident ledger.</li>
        <li>Drafting skills produce drawings and models that pass their own independent checks before release.</li>
      </ul>
      <p><a href="/pricing">Pricing</a> | <a href="/login">Sign in</a> | <a href="/terms">Terms</a> | <a href="/privacy">Privacy</a></p>
    </main>
  );
}
