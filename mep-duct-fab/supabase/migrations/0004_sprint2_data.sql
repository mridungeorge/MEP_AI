-- Sprint 2 (prove the data): building parts, ingest evidence, typed schedule inputs, Gate 1 confirmation.

-- 1. A building is a list of parts (class, storeys, area). project.building_class stays for old rows; the engine and the
--    UI read building_part. The parts choose the rule pack and the applicability check, so they freeze like the other
--    edition-selecting project facts.
create table building_part (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  project_id uuid not null,
  position int not null check (position >= 0),
  building_class text not null check (building_class in
    ('2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c')),
  storeys int check (storeys between 1 and 200),
  area_m2_value numeric check (area_m2_value > 0 and area_m2_value <= 10000000),
  area_m2_unit text not null default 'm^2' check (area_m2_unit = 'm^2'),
  area_m2_provenance provenance,
  confirmed_by uuid, confirmed_at timestamptz,
  check ((area_m2_value is null) = (area_m2_provenance is null)),
  check ((confirmed_by is null) = (confirmed_at is null)),
  constraint building_part_confirmed_by_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  constraint building_part_project_fk foreign key (project_id, firm_id) references project (id, firm_id),
  unique (project_id, position)
);

create function building_part_frozen_after_results() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare pid uuid; pfirm uuid;
begin
  pid := coalesce(new.project_id, old.project_id);
  pfirm := coalesce(new.firm_id, old.firm_id);
  if current_setting('role', true) in ('anon', 'authenticated') and pfirm is distinct from public.current_firm_id() then
    raise exception 'new row violates row-level security policy' using errcode = '42501';
  end if;
  if exists (select 1 from public.rule_result rr join public.revision r on r.id = rr.revision_id
              where r.project_id = pid and r.firm_id = pfirm)
     or exists (select 1 from public.revision r where r.project_id = pid and r.firm_id = pfirm
                 and r.frozen_at is not null) then
    raise exception 'building parts cannot change once results exist or a revision is frozen';
  end if;
  return coalesce(new, old);
end $$;
create trigger building_part_frozen before insert or update or delete on building_part
  for each row execute function building_part_frozen_after_results();
create trigger building_part_immutable_ids before update on building_part
  for each row execute function forbid_column_change('firm_id', 'project_id');

-- 1b. The facts that choose the NCC edition and the rule inputs (state, edition, climate zone, approval date) are
--     confirmed by a designer like any other Gate 1 value: the engine reads them only with confirmed_by set.
alter table project add column approval_date date,
  add column confirmed_by uuid, add column confirmed_at timestamptz,
  add constraint project_confirmed_by_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  add check ((confirmed_by is null) = (confirmed_at is null));
drop policy project_firm_insert on project;
drop policy project_firm_update on project;
create policy project_firm_insert on project for insert
  with check (firm_id = current_firm_id() and current_user_role() = 'designer');
create policy project_firm_update on project for update
  using (firm_id = current_firm_id() and current_user_role() = 'designer')
  with check (firm_id = current_firm_id() and current_user_role() = 'designer');

-- approval_date picks the edition in force, so it freezes with the other edition-selecting facts
create or replace function project_edition_frozen_after_results() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if (new.ncc_edition, new.state, new.climate_zone, new.building_class, new.approval_date)
       is distinct from (old.ncc_edition, old.state, old.climate_zone, old.building_class, old.approval_date)
     and (exists (select 1 from public.rule_result rr join public.revision r on r.id = rr.revision_id
                   where r.project_id = old.id)
          or exists (select 1 from public.revision r where r.project_id = old.id and r.frozen_at is not null))
  then
    raise exception 'project edition, state, climate zone, class and approval date cannot change once results exist or a revision is frozen';
  end if;
  return new;
end $$;

-- 2. Spaces carry their storey (IfcBuildingStorey) and who confirmed them at Gate 1.
alter table space add column storey text,
  add column confirmed_by uuid, add column confirmed_at timestamptz,
  add constraint space_confirmed_by_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  add check ((confirmed_by is null) = (confirmed_at is null)),
  add constraint space_area_sane check (area_m2_value is null or (area_m2_value > 0 and area_m2_value <= 10000000)),
  add constraint space_void_sane check (ceiling_void_mm_value is null or (ceiling_void_mm_value > 0 and ceiling_void_mm_value <= 100000));

-- 3. Schedule inputs: one typed row per input of a system. Never read provenance from system.controls (client jsonb).
create table system_input (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  system_id uuid not null,
  name text not null check (name ~ '^[a-z][a-z0-9_]{0,63}$'),
  value_number numeric,
  value_text text,
  value_bool boolean,
  unit text check (unit is null or length(unit) <= 64),
  provenance provenance not null,
  source text not null default 'form' check (source in ('form', 'xlsx', 'api', 'ifc', 'dxf', 'pdf')),
  confirmed_by uuid, confirmed_at timestamptz,
  check (num_nonnulls(value_number, value_text, value_bool) = 1),
  check (value_number is null or unit is not null),   -- every numeric input has a unit
  check ((confirmed_by is null) = (confirmed_at is null)),
  constraint system_input_system_fk foreign key (system_id, firm_id) references system (id, firm_id),
  constraint system_input_confirmed_by_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  unique (system_id, name)
);
create trigger system_input_immutable_ids before update on system_input
  for each row execute function forbid_column_change('firm_id', 'system_id');

-- the frozen-revision guard must also cover rows reached through a system (equipment, system_input)
create or replace function reject_if_revision_frozen() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid; frozen boolean; row_firm uuid;
begin
  row_firm := coalesce(new.firm_id, old.firm_id);
  if current_setting('role', true) in ('anon', 'authenticated')
     and row_firm is distinct from public.current_firm_id() then
    raise exception 'new row violates row-level security policy' using errcode = '42501';
  end if;
  if tg_table_name in ('equipment', 'system_input') then
    select s.revision_id into rev from public.system s
      where s.id = coalesce(new.system_id, old.system_id) and s.firm_id = row_firm;
  else
    rev := coalesce(new.revision_id, old.revision_id);
  end if;
  select r.frozen_at is not null into frozen from public.revision r
    where r.id = rev and r.firm_id = row_firm for share;
  if coalesce(frozen, false) then
    raise exception 'revision is frozen';
  end if;
  return coalesce(new, old);
end $$;
create trigger system_input_frozen before insert or update or delete on system_input
  for each row execute function reject_if_revision_frozen();

-- 4. Gate 1. Provenance is earned (0003): a client may only record 'default'. A designer confirms values by calling
--    gate1_confirm(), which checks the role and the firm, sets the confirmed kinds and records who and when. A client
--    cannot write confirmed_by/confirmed_at, and editing a row withdraws its confirmation. The engine reads a row only
--    when confirmed_by is set AND its provenance is a confirmed kind.
create function client_column_default_only() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$
declare c text;
begin
  if current_user in ('anon', 'authenticated') then
    foreach c in array tg_argv loop
      if to_jsonb(new) ->> c is not null and to_jsonb(new) ->> c <> 'default' then
        raise exception '% may only be set to default by a client (set by the API)', c;
      end if;
    end loop;
  end if;
  return new;
end $$;
create trigger system_input_client_provenance before insert or update on system_input
  for each row execute function client_column_default_only('provenance');
create trigger building_part_client_provenance before insert or update on building_part
  for each row execute function client_provenance_default_only();

create function gate1_confirmation_guard() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$
declare c text; changed boolean := false; client boolean := current_user in ('anon', 'authenticated');
begin
  if tg_op = 'INSERT' then
    if client and (new.confirmed_by is not null or new.confirmed_at is not null) then
      raise exception 'confirmed_by and confirmed_at are recorded by gate1_confirm, not written by a client';
    end if;
    return new;
  end if;
  if client and (new.confirmed_by is distinct from old.confirmed_by or new.confirmed_at is distinct from old.confirmed_at) then
    raise exception 'confirmed_by and confirmed_at are recorded by gate1_confirm, not written by a client';
  end if;
  foreach c in array tg_argv loop
    if to_jsonb(new) -> c is distinct from to_jsonb(old) -> c then changed := true; end if;
  end loop;
  -- an edit withdraws the confirmation for EVERY role (client, service role, any other); only a statement that also
  -- sets confirmed_by (gate1_confirm, or a service-role re-confirmation) keeps it
  if changed and new.confirmed_by is not distinct from old.confirmed_by then
    new.confirmed_by := null;
    new.confirmed_at := null;
  end if;
  return new;
end $$;
create trigger space_gate1 before insert or update on space
  for each row execute function gate1_confirmation_guard(
    'ifc_guid', 'name', 'use', 'storey', 'area_m2_value', 'area_m2_unit', 'area_m2_provenance',
    'ceiling_void_mm_value', 'ceiling_void_mm_unit', 'ceiling_void_mm_provenance');
create trigger project_gate1 before insert or update on project
  for each row execute function gate1_confirmation_guard(
    'state', 'ncc_edition', 'climate_zone', 'building_class', 'approval_date');
create trigger building_part_gate1 before insert or update on building_part
  for each row execute function gate1_confirmation_guard(
    'position', 'building_class', 'storeys', 'area_m2_value', 'area_m2_unit', 'area_m2_provenance');
create trigger system_input_gate1 before insert or update on system_input
  for each row execute function gate1_confirmation_guard(
    'name', 'value_number', 'value_text', 'value_bool', 'unit', 'provenance');

-- Deleting a part shifts the positions the subjects refer to: the remaining parts need confirming again.
create function building_part_delete_withdraws() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  update public.building_part set confirmed_by = null, confirmed_at = null
    where project_id = old.project_id and firm_id = old.firm_id and confirmed_by is not null;
  return old;
end $$;
create trigger building_part_delete_withdraw after delete on building_part
  for each row execute function building_part_delete_withdraws();
create function building_part_shift_withdraws() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  -- an inserted part or a changed position shifts the indices subjects refer to: confirm the list again
  if tg_op = 'INSERT' or new.position is distinct from old.position then
    update public.building_part set confirmed_by = null, confirmed_at = null
      where project_id = new.project_id and firm_id = new.firm_id and id <> new.id and confirmed_by is not null;
  end if;
  return new;
end $$;
create trigger building_part_shift_withdraw after insert or update on building_part
  for each row execute function building_part_shift_withdraws();

create function gate1_confirm(p_kind text, p_ids uuid[]) returns int language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare n int; wanted int;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer confirms values at gate 1' using errcode = '42501';
  end if;
  if p_ids is null or cardinality(p_ids) = 0 then
    raise exception 'no rows to confirm';
  end if;
  select count(distinct x) into wanted from unnest(p_ids) x;
  if p_kind = 'space' then
    update public.space set
      area_m2_provenance = case when area_m2_value is null then area_m2_provenance else 'engineer_confirmed' end,
      ceiling_void_mm_provenance = case when ceiling_void_mm_value is null then ceiling_void_mm_provenance
                                        else 'engineer_confirmed' end,
      confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id();
  elsif p_kind = 'project' then
    if exists (select 1 from public.project where id = any(p_ids) and firm_id = public.current_firm_id()
                 and (state is null or ncc_edition is null or climate_zone is null or approval_date is null)) then
      raise exception 'state, NCC edition, climate zone and approval date must all be set before they are confirmed';
    end if;
    update public.project set confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id();
  elsif p_kind = 'building_part' then
    update public.building_part set
      area_m2_provenance = case when area_m2_value is null then area_m2_provenance else 'engineer_confirmed' end,
      confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id();
  elsif p_kind = 'system_input' then
    update public.system_input set provenance = 'engineer_confirmed', confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id();
  else
    raise exception 'unknown kind %', p_kind;
  end if;
  get diagnostics n = row_count;
  if n <> wanted then
    raise exception 'some rows were not found in your firm';
  end if;
  return n;
end $$;
revoke all on function gate1_confirm(text, uuid[]) from public, anon;
grant execute on function gate1_confirm(text, uuid[]) to authenticated;

-- 5. Ingest evidence: what the readers (IFC, DXF, PDF vision, Excel) extracted. Append-only, written by the service
--    role only, always provenance 'extracted'. PDF vision writes here and nowhere else.
create function extraction_append_only() returns trigger language plpgsql as
$$ begin raise exception 'extraction rows are append-only evidence'; end $$;
create table ingest_run (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  source_kind text not null check (source_kind in ('ifc', 'dxf', 'pdf', 'xlsx')),
  source_name text not null,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  health jsonb,                       -- ingest/health.py output (score, checks, fixes); null for pdf/xlsx
  metadata jsonb not null default '{}',
  problems jsonb not null default '[]',
  created_at timestamptz not null default now(),
  constraint ingest_run_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint ingest_run_id_firm_uq unique (id, firm_id),
  unique (revision_id, source_sha256)  -- the same file is not ingested twice into one revision
);
create trigger ingest_run_frozen before insert on ingest_run
  for each row execute function reject_if_revision_frozen();
create trigger ingest_run_no_update before update or delete on ingest_run
  for each row execute function extraction_append_only();

create table extraction (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  ingest_run uuid not null,
  constraint extraction_run_fk foreign key (ingest_run, firm_id) references ingest_run (id, firm_id),
  source_kind text not null check (source_kind in ('ifc', 'dxf', 'pdf', 'xlsx')),
  source_name text not null,
  source_sha256 text not null check (source_sha256 ~ '^[0-9a-f]{64}$'),
  entity_kind text not null check (entity_kind in ('space', 'system', 'equipment')),
  entity_key text not null,
  field text not null,
  value_number numeric, value_text text, value_bool boolean,
  unit text,
  provenance provenance not null default 'extracted' check (provenance = 'extracted'),
  confidence numeric check (confidence between 0 and 1),
  raw jsonb not null default '{}',
  created_at timestamptz not null default now(),
  check (num_nonnulls(value_number, value_text, value_bool) = 1),
  check (value_number is null or unit is not null),
  constraint extraction_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id)
);
create trigger extraction_no_update before update or delete on extraction
  for each row execute function extraction_append_only();
create trigger extraction_frozen before insert on extraction
  for each row execute function reject_if_revision_frozen();

-- 6. RLS. Parts and schedule inputs are designer data (client-writable); extraction is service-written, read-only to clients.
do $$
declare t text;
begin
  foreach t in array array['building_part', 'system_input', 'extraction', 'ingest_run']
  loop
    execute format('alter table %I enable row level security', t);
    execute format('create policy %I on %I for select using (firm_id = current_firm_id())', t || '_firm_select', t);
  end loop;
  foreach t in array array['building_part', 'system_input']
  loop
    execute format('create policy %I on %I for insert with check (firm_id = current_firm_id() and current_user_role() = ''designer'')', t || '_firm_insert', t);
    execute format('create policy %I on %I for update using (firm_id = current_firm_id() and current_user_role() = ''designer'') with check (firm_id = current_firm_id() and current_user_role() = ''designer'')',
                   t || '_firm_update', t);
    execute format('create policy %I on %I for delete using (firm_id = current_firm_id() and current_user_role() = ''designer'')', t || '_firm_delete', t);
  end loop;
end $$;
revoke insert, update, delete on extraction, ingest_run from authenticated;
revoke all on building_part, system_input, extraction, ingest_run from anon;
revoke truncate, trigger, references on building_part, system_input, extraction, ingest_run from authenticated;
