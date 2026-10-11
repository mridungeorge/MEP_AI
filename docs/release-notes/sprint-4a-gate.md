# Release notes: sprint-4a-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Every signed package carries the DRAFT banner. Not a compliance certification.

## What this release delivers
- An exception classifier: each result is either a clean pass or falls into one of seven exception classes, with the reason stored.
- Gate 2 (checker) and Gate 3 (approver) sign-off: a worksheet, a random spot-check for bulk approval, gate order enforced, approver registration number required.
- A hash-chained audit ledger with verification.
- A signed package (JSON and a deterministic PDF, released only if its validator passes and the ledger verifies) and a read-only share page for a certifier.
- A browser test: designer, checker, approver, signed package, share link, revoke.

## What it does not do yet
- Share links carried the token in the URL (changed in sprint-4a1). The ledger detects edits but is not proof against the database owner.

## Verification
- Local gate; two review rounds, 4 blockers fixed. The round-2 fixes had no third review.

## Data and migrations
- Migrations 0010, 0013, 0014.

## Decisions needed from the engineer / owner
- Whether a checker may approve a FAIL (currently allowed and labelled "accepted FAIL").
