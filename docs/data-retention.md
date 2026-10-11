# Data retention (as built)

**LEGAL REVIEW REQUIRED.** This is a technical description of what the code does today. It is not a legal policy, not a privacy notice, and not advice. Nothing
here should be shown to customers as a commitment until the owner and a lawyer have decided the open items at the end.

Sources: `scripts/dataops.py`, `docs/runbooks/backup-restore.md`, the hash-chained ledger (migration 0010 onward). Operations are run by a person with the
database connection string; there is no self-service screen for them.

## What the product keeps
- **Signed record**: projects, revisions, rule results, reviews, sign-offs and the approver registration numbers as signed. Kept on retirement.
- **Audit ledger**: hash-chained events per firm. It holds user ids, not email addresses. Kept on retirement. A `firm_retired` entry records what was erased
  and the SHA-256 of the export manifest.
- **Personal data**: sign-in email addresses and notification preferences (`app_user`), invitations, notifications, registration evidence text.
- **Drawings and generated files**: architect models (`base_model`, `clash_model`), released skill outputs (`artifact_blob`), firm templates, uploaded IFC/DXF/PDF
  in Supabase Storage (referenced from `ingest_run`).
- **Transient**: queued drafting and PDF-reading jobs (`skill_job`, `vision_job`).
- **Tokens**: share links are stored as hashes; the export never copies a `token` column.

## Retention period
`RETENTION_YEARS_MIN = 7` in `scripts/dataops.py`. The retention clock starts at retirement (`firm.deleted_at`). `purge-firm` refuses a shorter
`--retention-years`, refuses a firm that was never retired, and refuses before `deleted_at + retention years`. The default is 7. The code does not know any
other retention rule; a longer period is set only by passing a larger number at purge time.

## What each operation does
| Operation | Effect |
|---|---|
| `backup` | `pg_dump` of the `public` schema, `auth_users.json` (id, address, confirmation) and a manifest (file hashes, row counts, each firm's ledger head, drawing checksums, ledger verdict). Passwords, sessions and MFA are the identity provider's and are not in it. Uploaded drawings in Storage are not in it. |
| `restore-check` | Restores a backup into an empty scratch database and compares rows, ledger heads, ledger verification and file checksums. Exit 1 on any difference. |
| `export-firm` | Writes every row of one firm as JSON lines, `people.json`, stored files, and (when `MEP_SUPABASE_URL` and `MEP_SUPABASE_SERVICE_KEY` are set) the uploaded drawings, with a manifest of hashes. Transient job tables are skipped. Nothing of another firm is included. |
| `retire-firm` | Requires a verified export of that firm and the firm name typed again. Deactivates and removes admin rights; clears email and notification preferences; deletes notifications, invitations, queued jobs, firm templates and share sessions; revokes certifier links; replaces registration evidence text and decision notes; deletes stored architect models, clash models and released files; bans and anonymises the sign-in accounts; sets `deleted_at`; writes the `firm_retired` ledger entry. Keeps the signed record and the ledger. |
| `purge-firm` | After the retention period only: deletes every remaining row of the firm, ledger included, then the firm. Append-only guards are switched off for this and switched back on in the same transaction. |

## What is not covered
- **Backups**: a purge or retirement does not touch earlier backups, local exports, or the provider's own backups; they hold the firm until they age out or are
  deleted by hand. The code sets no backup retention.
- **Uploaded drawings in Storage**: `retire-firm` deletes database rows; deletion of the Storage objects themselves is not performed by `dataops.py`. Confirm
  and delete them in the Storage console. (Verify before relying on this: check the runbook and the Storage bucket after a retirement.)
- Logs, error reports (Sentry) and the email provider's records are outside this tool.
- Registration numbers and the ledger's user ids remain in the signed record until purge.

## Open decisions for the owner (LEGAL REVIEW REQUIRED)
1. **Retention period**: is 7 years from retirement right for the signed record and the ledger? Who is the data controller, and does a longer period apply to firms'
   own obligations or insurers?
2. **Backup retention**: how long may backups and exports (yours, the provider's) live, and how are old ones deleted after a purge?
3. **Jurisdiction**: which law governs (Australian Privacy Act, state law, overseas hosting region of the database and Storage)? Where must data be hosted?
4. **Notification**: what must firms be told, and when, on retirement, on purge, and on a data breach? Who sends it?
5. **Erasure requests**: how to handle a person's request to erase data while the signed record and ledger must be kept.
6. **Storage objects**: confirm that uploaded drawings are deleted from Storage on retirement and who is responsible for it.
7. **Customer-facing text**: `/terms` and `/privacy` are placeholders; this document does not replace them.
