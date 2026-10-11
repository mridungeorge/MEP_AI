# Limits and what draft rules mean

## Draft rules

Every rule is status draft. The clause references and logic have been encoded, but no engineer has approved any rule. Only a named human engineer can approve a rule, in the repository, with a reviewer name and date. Nothing in this application, and no AI, can approve a rule.

This is why every report and PDF is stamped DRAFT RULES: NOT ENGINEER-APPROVED. Treat the application as a demonstration of traceable checking, not as a compliance tool. A PASS means only that the confirmed inputs satisfy the rule as encoded. Whether the rule as encoded matches the clause is the open question. Rule review packs for engineers are in docs/engineer-review.

## Standards and thresholds

- The app stores clause references and encoded logic, never the standard's wording.
- Thresholds must come from the official source. An unknown value is stored as TODO_FROM_SOURCE and gives no result.
- Licensed Australian Standards need a licence. The slots for AS 1668.2 and for the electrical standards are empty: no rule encodes them. Do not expect results against them.
- One NCC edition per project. NCC 2022 and NCC 2025 are separate packs.

## Other limits

- Fix suggestions are hypotheses to verify, not answers.
- Clash-lite uses boxes and gives warnings only.
- Sizing velocity limits have no default until your firm sets them.
- The 3D preview and the drafting worker need a browser with WebGL and a Docker host respectively.
- Terms of use and privacy pages are placeholders marked LEGAL REVIEW REQUIRED. The NSW declaration draft is a draft copy pending legal review.
- People join a firm by invitation only; there is no self sign-up.
- The near-miss firm default is stored but not yet applied by the engine.
- Billing runs in Stripe test mode only.

## What the app will refuse, in one place

An unconfirmed input, an extracted value used as an input, a unit mismatch as a pass, a change to a frozen revision, a bulk-accepted FAIL, a sign-off from the wrong role, an agent-made outcome, and any file whose independent check failed.
