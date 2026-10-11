# Release notes: sprint-4a1-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. The demo project is synthetic. Not a compliance certification.

## What this release delivers
- Signer independence as a firm setting: strict (three different people) or small firm (one person acting in several roles, with every record marked NOT INDEPENDENTLY CHECKED).
- Accepted FAIL: approving a FAIL needs a category and explanation, never in bulk, and the approver acknowledges each one before Gate 3. Disputed rules are listed for the engineer.
- Share links with the token in the URL fragment and a short-lived cookie session; every read is logged.
- Approver registration numbers are set by the service only (runbook, break-glass script).
- Deployment files (API Dockerfile, Railway config, env templates, runbooks) and a demo script with synthetic models.

## What it does not do yet
- The deployment Dockerfile had not been built when tagged. The throttle is per replica.

## Verification
- Local gate; round 1 found 9 should-fix items, round 2 one blocker; all fixed. Round-2 fixes had no third review.

## Data and migrations
- Migrations 0016, 0017.
