-- Phase 6.6: a retired firm keeps its signed record (see docs/runbooks/backup-restore.md) but nobody can sign in and no new work can start.
alter table firm add column deleted_at timestamptz;
