# Migration runbook

Migrations are the numbered files in `supabase/migrations/`. They are applied in name order, once. The database is the safety net
(row-level security, the sign-off state machine, the hash-chained ledger), so migrations are reviewed like code.

## Rules

1. **Never edit a migration that has been applied anywhere** (your laptop counts if you keep data there; Supabase cloud always counts).
   Fix forward with a NEW file with the next number, using `create or replace function` / `alter table`.
2. Test every migration from scratch **and** on top of data: `supabase db reset` (local, applies all) and the full test run
   (`scripts/ci.sh`). Tests that insert ledger rows leave them behind; that is expected (the ledger is append-only).
3. Anything that touches the ledger (`ledger_event`, hash columns, triggers) needs a backfill plan and a `verify_ledger` run afterwards.
   0010 shows the pattern: compute hashes in order under the immutability bypass inside the migration, then remove the bypass (0013).
4. A migration may not approve a rule, change a rule file, or touch a sign-off row except to ADD a column with a default.
5. Roll forward only. If a migration is wrong, write the next one; do not `reset` a cloud database that has data.

## Applying to the cloud project

```
supabase link --project-ref <project-ref>        # once per machine
supabase migration list                           # local and remote columns should match up to the last applied
supabase db push --dry-run                        # shows exactly which files would run; read it
# take a backup first (Dashboard > Database > Backups) when the project holds data you care about
supabase db push
```

After it: run `select * from verify_ledger('<firm uuid>');` as the service role for each firm (expects `ok = true`), open the app, and
run the smoke test in `deploy.md`.

## When something fails halfway

`supabase db push` runs each file in a transaction: a failed file leaves nothing behind. Fix the file only if it never succeeded anywhere;
otherwise add a new migration. If the CLI says the remote history differs from local, **stop** and compare `supabase migration list`
before using `supabase migration repair`.

## Known one-off effects

- 0011: results stored before it have no input fingerprint; an open revision must be re-run once before it can be frozen.
- 0013/0015: the ledger gains the `ledger_head` row per firm and `signer_mode`; nothing to do by hand.
- 0015 drops `open_share_link`: old share links stop working until the new web build is deployed (links created earlier keep
  their token; only the URL shape changes from `/share/<token>` to `/share#<token>`: re-create links you have already sent).
