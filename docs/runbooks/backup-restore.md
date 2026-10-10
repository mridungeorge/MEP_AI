# Backup, restore, firm export and firm retirement

Tool: `scripts/dataops.py` (needs `pg_dump`/`pg_restore` on the PATH, version 15 or newer; `--docker <container>` runs them inside a Postgres container).
Tested by `tests/rls/test_pg_dataops.py`: a backup of a live database is restored into a scratch database and every row count, every ledger chain and every
stored file is compared with the backup's manifest.

## What a backup holds
`public.dump` (the whole `public` schema, custom format), `auth_users.json` (id, address, confirmation: enough for people to sign in again by magic
link; passwords, sessions and MFA are the provider's, covered by Supabase's own daily backups on the Pro plan), and `manifest.json` (SHA-256 of each file,
row count per table, every firm's ledger head, a checksum of every stored drawing and the ledger verdict per firm). Uploaded drawings (IFC/DXF/PDF) live in
Supabase Storage: they are listed in `ingest_run.metadata.storage_path` and are copied by `export-firm` (needs `MEP_SUPABASE_SERVICE_KEY` on your machine).

## Take a backup (weekly, and before every migration on real data)
```
export MEP_DB_URL='<direct or session-pooler connection string>'
uv run python scripts/dataops.py backup --out backups/$(date +%F)
```
Keep the folder somewhere other than the laptop (an encrypted bucket you own). The Supabase Pro daily backup is the safety net; this one is yours.

## Prove a backup restores (do it monthly, and once before the pilot)
1. Create a **scratch Supabase project** (free tier is fine; name it `mep-restore-test`; delete it afterwards).
2. Apply the migrations to it: `supabase link --project-ref <scratch-ref>` then `supabase db push`.
3. Restore and compare:
```
uv run python scripts/dataops.py restore-check --dir backups/2026-10-12 --target-dsn '<scratch direct connection string>'
```
It refuses a database that already holds data, restores `auth` identities then the `public` data with triggers off (so ledger rows come back exactly as
dumped: `session_replication_role = replica`; if your database role may not set it, restore as the project's superuser/`supabase_admin` role via the
direct connection), then prints `restore verified: ...` or `FAIL: ...` for each difference (exit status 1). Exit status 0 means: same rows, same ledger heads,
the ledger chains verify exactly as in the source, and every stored file has the same checksum.
4. Delete the scratch project.

If the real database is ever lost: create a new project, `supabase db push`, run the same `restore-check` command against it with `--allow-nonempty` only if you
deliberately seeded it, point `MEP_DB_URL` at it, and ask people to sign in again.

## Export one firm's data (the firm's right to its records)
```
uv run python scripts/dataops.py export-firm --firm "Exact Firm Name" --out exports/firm-name
```
Writes `data/<table>.jsonl` (every row of the firm, no tokens), `people.json`, `files/` (released drawings and templates; uploaded architect files when the
Supabase service key is set) and `manifest.json` with the SHA-256 of each. Nothing of any other firm is included. Hand the folder to the firm.

## Retire a firm (offboarding) and what is kept
`retire-firm` requires an export of that firm made first (it checks the manifest and the firm id) and the firm name typed again. It then:
- **erases**: e-mail addresses (app_user, invitations, notifications), stored drawings and generated files (`artifact_blob`), templates, queued jobs and the
  evidence text of registration submissions; closes every account (deactivated, banned, identity anonymised in Auth);
- **keeps**: the signed record (projects, revisions, rule results, reviews, sign-offs, registration numbers as signed) and the hash-chained audit ledger,
  which holds user ids but no e-mail addresses. A `firm_retired` ledger entry records what was erased and the checksum of the export.
- **Why keep them:** a signed compliance package may have to be substantiated years later. Retention: **7 years from retirement** (the minimum the tool
  accepts; your firms' own obligations may be longer: agree the period in the pilot agreement, LEGAL REVIEW REQUIRED).

`purge-firm` deletes the remaining rows of a retired firm, ledger included, and refuses before the retention period has passed or if the firm was never
retired. After a purge nothing of the firm remains in the database; backups made earlier still hold it until they age out (delete them per your policy).
