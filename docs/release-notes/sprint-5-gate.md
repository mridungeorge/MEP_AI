# Release notes: sprint-5-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. No new features; hardening only. Not a compliance certification.

## What this release delivers
- Evidence spaces: a space added from a drawing reading stays "extracted", links to its source page, carries the unit the designer declared, and needs Gate 1.
- Agent builds need a designer to confirm the exact card version first.
- Drafting builds run through a job queue and a dispatcher in isolated containers (non-root, no network, read-only). A Docker host is required; Railway cannot run it.
- PDF reading is a background job with visible status. Agent notes written after a freeze are marked post-freeze and left out of the signed package.
- CI builds the API, web and worker images; a staging smoke script runs against any URL.

## What it does not do yet
- Images could not be built locally at this tag; CI was their first build. Other known nits are in docs/STATUS.md ("Phase 5 review record").

## Verification
- One consolidated review (4 reviewers) plus a round 2; CI found two real bugs (unreadable worker output, missing pypdf), both fixed. Round-2 fixes had no further review.

## Data and migrations
- Migrations 0022 to 0028.
