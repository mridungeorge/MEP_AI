-- Phase 5: reading a PDF drawing (render + vision) is a background job. The upload stores the file and returns at once; this table is the job and its
-- visible status. Only the service role writes it; people of the firm read the status.
create table vision_job (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  requested_by uuid not null,
  name text not null check (length(name) between 1 and 255),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  storage_path text not null,
  pdf bytea,                                        -- kept only until the job finishes
  status text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  error text,
  problems jsonb not null default '[]',
  ingest_run uuid,
  extractions int,
  attempts int not null default 0,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  constraint vision_job_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint vision_job_user_fk foreign key (requested_by, firm_id) references app_user (id, firm_id),
  unique (revision_id, sha256)
);
create index vision_job_queue_idx on vision_job (status, created_at);
alter table vision_job enable row level security;
create policy vision_job_firm_select on vision_job for select using (firm_id = current_firm_id());
revoke all on vision_job from anon;
revoke insert, update, delete, truncate, trigger, references on vision_job from authenticated;
-- the bytes are not a thing a client can read back: select is granted per column, without `pdf`
revoke select on vision_job from authenticated;
grant select (id, firm_id, revision_id, requested_by, name, sha256, storage_path, status, error, problems, ingest_run, extractions, attempts,
              created_at, started_at, finished_at) on vision_job to authenticated;
