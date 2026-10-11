# Release notes: sprint-7-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Fix suggestions are hypotheses only. Not a compliance certification.

## What this release delivers
- Fix hypotheses: for a FAIL, the engine searches for the smallest single-input change, accepted only if no dependent rule gets worse or still fails. Every option is labelled "Hypothesis: verify". Applying one writes an unconfirmed value that must pass Gate 1 again.
- Performance Solution pathway: a flag per result, engineer evidence kept append-only and never read by the engine, and a JSON/XLSX starting package carrying the draft banner.
- Services schedule (ducts, fittings, terminals), ceiling-void check against firm clearance, and quantities in CSV/XLSX. Results are CLASH, CLEAR or NO DATA, never PASS or FAIL.
- Clash-lite on IFC bounding boxes with BCF 2.1 export, warnings only.

## What it does not do yet
- Clash detection is simple axis-aligned boxes. Accepted fix options may leave a dependent rule at NEEDS_JUDGEMENT. Other limits are in docs/STATUS.md (Phase 7).

## Verification
- Two review rounds (12 then 6 findings), all fixed; the round-2 fixes were not re-reviewed.

## Data and migrations
- Migrations 0037 to 0039 (0037 and 0039 edited in place before any deployment).
