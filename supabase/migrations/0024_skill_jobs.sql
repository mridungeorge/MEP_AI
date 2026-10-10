-- Phase 5: drafting builds run in a separate worker. The API puts a job in this queue and waits for the answer; the dispatcher (service role)
-- takes jobs, runs each in an isolated container and writes the outcome back. Clients never touch these tables.
create table skill_job (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  requested_by uuid not null,
  skill text not null check (skill ~ '^[a-z][a-z0-9-]{1,40}$'),
  spec jsonb not null,
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  locked_by text,
  result jsonb,
  error text,
  constraint skill_job_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint skill_job_user_fk foreign key (requested_by, firm_id) references app_user (id, firm_id)
);
create index skill_job_queue_idx on skill_job (status, created_at);
create table skill_job_file (
  job_id uuid not null references skill_job (id) on delete cascade,
  firm_id uuid not null references firm(id),
  name text not null,
  content bytea not null,
  primary key (job_id, name)
);
alter table skill_job enable row level security;
alter table skill_job_file enable row level security;
revoke all on skill_job, skill_job_file from anon, authenticated;
