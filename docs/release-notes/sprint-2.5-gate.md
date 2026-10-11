# Release notes: sprint-2.5-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Golden projects are SYNTHETIC. Not a compliance certification.

## What this release delivers
- Sign-in by emailed magic link, a role shown in the header, protected routes.
- Upload of IFC and DXF files (content checked, 50 MiB cap, stored under the firm), then ingest and a model health score.
- A building part per system, so mixed-use projects run per part (any refused part refuses the run).
- A browser test of the whole path: sign in, upload, Gate 1, cited DRAFT report.

## What it does not do yet
- Failed ingests can leave orphaned stored files; the web token sits in browser local storage (Supabase default).

## Verification
- Local gate green (see docs/STATUS.md, "Phase 2.5"). Review round 1 found a DXF parser-hang blocker, round 2 an unbounded sandbox output; both fixed.
  The round-2 fixes had no third review.

## Data and migrations
- Migration 0009 added.
