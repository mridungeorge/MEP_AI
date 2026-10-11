# Release notes: sprint-3-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. The revision golden pair (Rev B to Rev C) is SYNTHETIC. Not a compliance certification.

## What this release delivers
- Revisions: uploading a new model onto a frozen revision creates a child revision, carrying over confirmations for spaces that did not change.
- A revision diff (GUID, else name and position), the set of results made stale by the change, and a reasoning trace for why a result changed.
- Cross-rule re-run that names conflicts between rules per input.
- Freeze with an input fingerprint so a frozen revision's results cannot be rewritten.
- A revision screen with lineage, diff, trace, conflicts, results, confirm and freeze.

## What it does not do yet
- Traces list stale results only; untagged systems are diffed by id.

## Verification
- Local gate; two review rounds (5 blockers in total, all fixed). The round-2 fixes had no third review; covered by the RLS and lineage tests.

## Data and migrations
- Migrations 0011 and 0012 (round-1 and round-2 fixes: frozen-result guards, input fingerprint, lock order); see docs/STATUS.md.
