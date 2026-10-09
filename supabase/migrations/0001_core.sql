-- MEP Co-pilot core schema. Every table is RLS-scoped by firm_id.
-- Numeric inputs are stored as (value, unit, provenance) column triples.

create type provenance as enum ('engineer_confirmed', 'address_lookup_confirmed', 'extracted', 'calculated', 'default');
create type user_role as enum ('designer', 'checker', 'approver');
create type rule_result_kind as enum ('PASS', 'FAIL', 'NEEDS_JUDGEMENT', 'NOT_APPLICABLE');
create type gate as enum ('gate1', 'gate2', 'gate3');

create table firm (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid generated always as (id) stored,
  name text not null,
  sample_size int not null default 10 check (sample_size >= 1)
);

create table app_user (
  id uuid primary key references auth.users(id),
  firm_id uuid not null references firm(id),
  role user_role not null,
  registration_no text
);

-- Firm comes from the user's app_user row, never from a client-editable JWT claim.
-- security definer so the lookup is not itself blocked by app_user's RLS.
create function current_firm_id() returns uuid language sql stable security definer
  set search_path = public, pg_temp as
$$ select firm_id from public.app_user where id = auth.uid() $$;

create function current_user_role() returns user_role language sql stable security definer
  set search_path = public, pg_temp as
$$ select role from public.app_user where id = auth.uid() $$;

create table project (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  address text not null,
  state text not null check (state in ('NSW','VIC','QLD','WA','SA','TAS','ACT','NT')),
  climate_zone int check (climate_zone between 1 and 8),
  building_class text,
  ncc_edition text not null check (ncc_edition in ('NCC2022', 'NCC2025'))
);

create table revision (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  project_id uuid not null references project(id),
  architect_rev text not null,
  status text not null default 'open',
  frozen_at timestamptz,
  parent_revision_id uuid references revision(id)
);

create table space (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  ifc_guid text,
  name text,
  area_m2_value numeric, area_m2_unit text default 'm^2', area_m2_provenance provenance,
  use text,
  ceiling_void_mm_value numeric, ceiling_void_mm_unit text default 'mm', ceiling_void_mm_provenance provenance
);

create table system (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  type text not null,
  airside_airflow_ls_value numeric, airside_airflow_ls_unit text default 'L/s', airside_airflow_ls_provenance provenance,
  capacity_kw_value numeric, capacity_kw_unit text default 'kW', capacity_kw_provenance provenance,
  fan_power_kw_value numeric, fan_power_kw_unit text default 'kW', fan_power_kw_provenance provenance,
  controls jsonb not null default '{}'
);

create table equipment (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  system_id uuid not null references system(id),
  tag text,
  kind text,
  attributes jsonb not null default '{}'
);

create table rule_result (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  rule_id text not null,
  result rule_result_kind not null,
  inputs jsonb not null,
  citation jsonb not null,
  fix_hypotheses jsonb not null default '[]',
  review_class text,
  stale boolean not null default false
);

create table artifact (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  kind text not null,
  path text not null,
  checksum text not null,
  validator jsonb not null,
  released boolean not null default false,
  -- an artifact is only releasable when its validator passed
  check (not released or coalesce((validator -> 'passed') = 'true'::jsonb, false))
);

create table review (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  rule_result_id uuid references rule_result(id),
  gate gate not null,
  user_id uuid not null references app_user(id),
  decision text not null,
  reason text
);

create table signoff (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  gate gate not null,
  user_id uuid not null references app_user(id),
  signed_at timestamptz not null default now(),
  unique (revision_id, gate)
);

create table ledger_event (
  id bigint generated always as identity primary key,
  firm_id uuid not null references firm(id),
  revision_id uuid references revision(id),
  kind text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create function ledger_event_immutable() returns trigger language plpgsql as
$$ begin raise exception 'ledger_event is append-only'; end $$;

create trigger ledger_event_no_update before update or delete on ledger_event
  for each row execute function ledger_event_immutable();
create trigger ledger_event_no_truncate before truncate on ledger_event
  for each statement execute function ledger_event_immutable();

create table ledger_link (
  token text primary key,
  firm_id uuid not null references firm(id),
  revision_id uuid not null references revision(id),
  expires_at timestamptz not null
);

-- A released artifact is frozen: its file cannot be swapped or un-validated.
create function artifact_frozen_once_released() returns trigger language plpgsql as
$$ begin
  if old.released and (new.path, new.checksum, new.validator, new.released)
       is distinct from (old.path, old.checksum, old.validator, old.released) then
    raise exception 'released artifact is immutable';
  end if;
  return new;
end $$;
create trigger artifact_frozen before update on artifact
  for each row execute function artifact_frozen_once_released();

create function gate_role(g gate) returns user_role language sql immutable as
$$ select case g when 'gate1' then 'designer'::user_role
                 when 'gate2' then 'checker'::user_role
                 else 'approver'::user_role end $$;

-- RLS on every table, firm_id isolation.
do $$
declare t text;
begin
  foreach t in array array['firm','app_user','project','revision','space','system','equipment',
    'rule_result','artifact','review','signoff','ledger_event','ledger_link']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select using (firm_id = current_firm_id())',
                   t || '_firm_select', t);
  end loop;
end $$;

-- Client-writable tables (designer data). Everything else is written only by the API
-- with the service role: rule_result (engine), artifact (validator gate), ledger_event,
-- ledger_link, app_user, firm. A client can never write a compliance result.
do $$
declare t text;
begin
  foreach t in array array['project','revision','space','system','equipment']
  loop
    execute format('create policy %I on %I for insert with check (firm_id = current_firm_id())',
                   t || '_firm_insert', t);
    execute format('create policy %I on %I for update using (firm_id = current_firm_id() and %s) with check (firm_id = current_firm_id())',
                   t || '_firm_update', t,
                   case when t = 'revision' then 'frozen_at is null'
                        else 'true' end);
  end loop;
end $$;

-- Reviews and sign-offs: only as yourself, only for the gate your role holds.
create policy review_insert on review for insert with check (
  firm_id = current_firm_id() and user_id = auth.uid() and current_user_role() = gate_role(gate));
create policy signoff_insert on signoff for insert with check (
  firm_id = current_firm_id() and user_id = auth.uid() and current_user_role() = gate_role(gate));
