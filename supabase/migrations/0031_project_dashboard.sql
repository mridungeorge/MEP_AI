-- Phase 6.3: the projects dashboard needs to know when things happened.
alter table revision add column created_at timestamptz not null default now();
alter table project add column created_at timestamptz not null default now();
-- existing revisions get the time of their first ledger entry where there is one
update revision r set created_at = coalesce((select min(created_at) from ledger_event l where l.revision_id = r.id), r.created_at);
update project p set created_at = coalesce((select min(r.created_at) from revision r where r.project_id = p.id), p.created_at);
create index revision_project_created on revision (project_id, created_at);
