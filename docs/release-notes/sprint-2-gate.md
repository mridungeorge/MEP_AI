# Release notes: sprint-2-gate (2026-10-09)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Golden projects are SYNTHETIC; no real pilot project has been run. Not a compliance certification.

## What this release delivers
- The rules engine over 24 draft rule files: units are mandatory, expressions run in a safe whitelist evaluator, every result carries a citation, and a report
  shows the DRAFT banner unless every rule used is approved (none are).
- Jurisdiction and building-class refusal: the engine refuses combinations it cannot support rather than guessing an edition.
- Reading architect data: IFC and DXF spaces, a health score for the model, an Excel schedule template per edition.
- Gate 1: a designer confirms project facts, building parts, spaces and schedule rows before any rule may use them. Extracted values are never inputs.
- A first web UI (Gate 1 confirm actions, report view) and a Postgres-backed API with row-level security.

## What it does not do yet
- No sign-in screen, no upload endpoint in the UI at this tag (added in sprint-2.5).
- PDF reading is not wired; DWG is refused; revit/archicad IFC profiles are unverified.
- GitHub CI had not run; the local devcontainer gate is the reference.

## Verification
- `scripts/ci.sh` green in the devcontainer on a fresh clone (details in docs/STATUS.md, "Sprint 2 finish"). Two review rounds; the last round's fixes had no third review.

## Data and migrations
- Rebuilt after a device change; history before this tag was lost (see "Recovery 2026-10-09" in docs/STATUS.md).

## Decisions needed from the engineer / owner
- Engineer review of all 24 drafts and typed thresholds; five real golden projects from a pilot firm.
