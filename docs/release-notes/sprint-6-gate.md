# Release notes: sprint-6-gate (2026-10-10)

ALL RULES ARE DRAFT AND NOT ENGINEER-APPROVED. Terms and privacy pages are placeholders marked LEGAL REVIEW REQUIRED. Stripe is test mode only. Not a compliance certification.

## What this release delivers
- Firm administration: invitations, roles, deactivation, firm settings, title block and layer standard uploads.
- Approver registration: the firm submits, a platform administrator verifies, ledgered.
- Projects dashboard with filters, revision history and who signed what.
- Email notifications (database outbox, preferences, Resend sender); observability (Sentry with PII scrubbing, JSON logs, health endpoints).
- Data safety tooling: backup, restore check, per-firm export, firm retirement and purge (see docs/data-retention.md).
- Billing in Stripe test mode with a gate on new projects only; signed work is never locked.

## What it does not do yet
- No self-signup; the near-miss firm default is stored but not applied by the engine; Sentry, Resend and Stripe are tested with fakes only (no live keys).

## Verification
- Two review rounds (2 blockers in round 1). CI green on 9f4b839. Round-2 fixes had no further review.

## Data and migrations
- Migrations 0029 to 0036. Open revisions with no input fingerprint should be re-run once before freezing.
