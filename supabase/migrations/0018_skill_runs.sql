-- Phase 4b: drafting skills run by the app. A run records the spec and what happened; each file becomes an `artifact` row, `released` only
-- when the independent validator passed, and its bytes (artifact_blob) exist ONLY for released artifacts: a build the validator rejected
-- is never available to anybody.

create table skill_run (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  skill text not null check (skill ~ '^[a-z][a-z0-9-]{1,40}$'),
  spec jsonb not null,
  spec_sha256 text not null check (spec_sha256 ~ '^[0-9a-f]{64}$'),
  status text not null check (status in ('ok', 'spec_rejected', 'validator_rejected', 'build_failed', 'timeout', 'revalidation_failed')),
  message text,
  validation jsonb not null default '{}',
  requested_by uuid not null,
  requested_via text not null default 'user' check (requested_via in ('user', 'agent')),
  created_at timestamptz not null default now(),
  constraint skill_run_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint skill_run_user_fk foreign key (requested_by, firm_id) references app_user (id, firm_id),
  constraint skill_run_id_firm_uq unique (id, firm_id)
);
alter table skill_run enable row level security;
create policy skill_run_firm_select on skill_run for select using (firm_id = current_firm_id());
revoke all on skill_run from anon;
revoke insert, update, delete, truncate, trigger, references on skill_run from authenticated;
create trigger skill_run_frozen before insert on skill_run for each row execute function reject_if_revision_frozen();
create trigger skill_run_append_only before update or delete on skill_run for each row execute function review_append_only();
create trigger skill_run_audit after insert on skill_run for each row execute function audit_row();

alter table artifact add column run_id uuid, add column name text, add column media_type text;
alter table artifact add constraint artifact_run_fk foreign key (run_id, firm_id) references skill_run (id, firm_id);

create table artifact_blob (
  artifact_id uuid primary key references artifact(id),
  firm_id uuid not null references firm(id),
  content bytea not null check (octet_length(content) between 1 and 52428800),
  media_type text not null
);
alter table artifact_blob enable row level security;
create policy artifact_blob_released_select on artifact_blob for select
  using (firm_id = current_firm_id() and exists (select 1 from artifact a where a.id = artifact_id and a.released and a.firm_id = firm_id));
revoke all on artifact_blob from anon;
revoke insert, update, delete, truncate, trigger, references on artifact_blob from authenticated;
create function artifact_blob_only_if_released() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if not exists (select 1 from public.artifact a where a.id = new.artifact_id and a.firm_id = new.firm_id and a.released
                   and coalesce((a.validator -> 'passed') = 'true'::jsonb, false)) then
    raise exception 'a file is stored only for an artifact whose validator passed';
  end if;
  return new;
end $$;
create trigger artifact_blob_guard before insert on artifact_blob for each row execute function artifact_blob_only_if_released();
create trigger artifact_blob_append_only before update or delete on artifact_blob for each row execute function review_append_only();
revoke all on function artifact_blob_only_if_released() from public, anon, authenticated;

-- Firm defaults for the fields a skill card marks `x-firm-default` (never for engineer inputs).
create table firm_skill_defaults (
  firm_id uuid not null references firm(id),
  skill text not null check (skill ~ '^[a-z][a-z0-9-]{1,40}$'),
  defaults jsonb not null,
  updated_by uuid not null,
  updated_at timestamptz not null default now(),
  primary key (firm_id, skill),
  constraint firm_skill_defaults_user_fk foreign key (updated_by, firm_id) references app_user (id, firm_id)
);
alter table firm_skill_defaults enable row level security;
create policy firm_skill_defaults_select on firm_skill_defaults for select using (firm_id = current_firm_id());
revoke all on firm_skill_defaults from anon;
revoke insert, update, delete, truncate, trigger, references on firm_skill_defaults from authenticated;
create trigger firm_skill_defaults_audit after insert or update on firm_skill_defaults for each row execute function audit_row();
