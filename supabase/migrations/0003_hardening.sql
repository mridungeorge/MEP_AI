-- Second adversarial review: frozen revisions, edition mixing, privileges, review integrity.

-- Identity columns never change after insert (stops moving a revision between projects/editions,
-- moving child rows between revisions, or handing a row to another firm).
create function forbid_column_change() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$
declare c text;
begin
  foreach c in array tg_argv loop
    if to_jsonb(new) -> c is distinct from to_jsonb(old) -> c then
      raise exception '%.% cannot be changed after insert', tg_table_name, c;
    end if;
  end loop;
  return new;
end $$;

create trigger revision_immutable_ids before update on revision
  for each row execute function forbid_column_change('firm_id', 'project_id', 'parent_revision_id');
create trigger space_immutable_ids before update on space
  for each row execute function forbid_column_change('firm_id', 'revision_id');
create trigger system_immutable_ids before update on system
  for each row execute function forbid_column_change('firm_id', 'revision_id');
create trigger equipment_immutable_ids before update on equipment
  for each row execute function forbid_column_change('firm_id', 'system_id');
create trigger project_immutable_ids before update on project
  for each row execute function forbid_column_change('firm_id');

-- A frozen revision's inputs cannot change: no insert, update or delete of its spaces, systems
-- or equipment, for any role.
create function reject_if_revision_frozen() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid; frozen boolean; row_firm uuid;
begin
  row_firm := coalesce(new.firm_id, old.firm_id);
  if current_setting('role', true) in ('anon', 'authenticated')
     and row_firm is distinct from public.current_firm_id() then
    raise exception 'new row violates row-level security policy' using errcode = '42501';
  end if;
  if tg_table_name = 'equipment' then
    select s.revision_id into rev from public.system s
      where s.id = coalesce(new.system_id, old.system_id) and s.firm_id = row_firm;
  else
    rev := coalesce(new.revision_id, old.revision_id);
  end if;
  -- only look at a revision of the row's own firm: another firm's revision is neither locked
  -- nor revealed (the composite foreign key rejects the row afterwards)
  select r.frozen_at is not null into frozen from public.revision r
    where r.id = rev and r.firm_id = row_firm for share;
  if coalesce(frozen, false) then
    raise exception 'revision is frozen';
  end if;
  return coalesce(new, old);
end $$;

create trigger space_frozen before insert or update or delete on space
  for each row execute function reject_if_revision_frozen();
create trigger system_frozen before insert or update or delete on system
  for each row execute function reject_if_revision_frozen();
create trigger equipment_frozen before insert or update or delete on equipment
  for each row execute function reject_if_revision_frozen();

-- Freezing and status are server actions: a client may not set or change them.
create function revision_server_only_columns() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$ begin
  if current_user in ('anon', 'authenticated') then
    if tg_op = 'INSERT' and (new.frozen_at is not null or new.status <> 'open') then
      raise exception 'frozen_at and status are set by the server';
    elsif tg_op = 'UPDATE' and (new.frozen_at, new.status) is distinct from (old.frozen_at, old.status) then
      raise exception 'frozen_at and status are set by the server';
    end if;
  end if;
  return new;
end $$;
create trigger revision_server_only before insert or update on revision
  for each row execute function revision_server_only_columns();

-- Project facts that select rules (edition, state, climate zone, class) freeze once results exist.
create or replace function project_edition_frozen_after_results() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if (new.ncc_edition, new.state, new.climate_zone, new.building_class)
       is distinct from (old.ncc_edition, old.state, old.climate_zone, old.building_class)
     and (exists (select 1 from public.rule_result rr join public.revision r on r.id = rr.revision_id
                   where r.project_id = old.id)
          or exists (select 1 from public.revision r where r.project_id = old.id and r.frozen_at is not null))
  then
    raise exception 'project edition, state, climate zone and class cannot change once results exist or a revision is frozen';
  end if;
  return new;
end $$;

-- A review is always about a result.
alter table review alter column rule_result_id set not null;

-- Closed domains instead of free text (silent NOT_APPLICABLE on a typo otherwise).
alter table project add constraint project_building_class_domain
  check (building_class is null or building_class in
    ('2', '3', '4', '5', '6', '7a', '7b', '8', '9a', '9b', '9c'));
alter table system add constraint system_type_domain
  check (type in ('air_conditioning', 'air_conditioning_heating', 'mechanical_ventilation', 'exhaust'));

-- Supabase grants anon/authenticated everything on new tables. RLS covers row access, but
-- TRUNCATE, TRIGGER and REFERENCES bypass or subvert it, so take them away, and give anon nothing.
revoke all on all tables in schema public from anon;
revoke truncate, trigger, references on all tables in schema public from authenticated;
revoke insert, update, delete on rule_result, artifact, ledger_event, ledger_link, app_user, firm
  from authenticated;
alter default privileges in schema public revoke all on tables from anon;
alter default privileges in schema public revoke truncate, trigger, references on tables from authenticated;

-- A revision's parent must be in the same project (so a diff never spans two NCC editions).
create function revision_parent_same_project() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if current_setting('role', true) in ('anon', 'authenticated')
     and new.firm_id is distinct from public.current_firm_id() then
    raise exception 'new row violates row-level security policy' using errcode = '42501';
  end if;
  if new.parent_revision_id is not null and not exists (
       select 1 from public.revision p where p.id = new.parent_revision_id and p.project_id = new.project_id) then
    raise exception 'parent revision must belong to the same project';
  end if;
  return new;
end $$;
create trigger revision_parent_check before insert on revision
  for each row execute function revision_parent_same_project();

-- Provenance is earned, not typed: a client may only record 'default' provenance. engineer_confirmed,
-- address_lookup_confirmed, calculated and extracted are written by the API (gate 1 and the
-- ingest/calculation services), which is the only path the engine will trust.
create function client_provenance_default_only() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$
declare k text; v text;
begin
  if current_user in ('anon', 'authenticated') then
    for k, v in select key, value from jsonb_each_text(to_jsonb(new)) where key like '%\_provenance' loop
      if v is not null and v <> 'default' then
        raise exception '% may only be set to default by a client (set by the API)', k;
      end if;
    end loop;
  end if;
  return new;
end $$;
create trigger space_client_provenance before insert or update on space
  for each row execute function client_provenance_default_only();
create trigger system_client_provenance before insert or update on system
  for each row execute function client_provenance_default_only();

-- Units are fixed per column (the API converts before storing).
alter table space
  add constraint space_area_unit check (area_m2_unit = 'm^2'),
  add constraint space_ceiling_unit check (ceiling_void_mm_unit = 'mm');
alter table system
  add constraint system_airflow_unit check (airside_airflow_ls_unit = 'L/s'),
  add constraint system_capacity_unit check (capacity_kw_unit = 'kW'),
  add constraint system_fan_power_unit check (fan_power_kw_unit = 'kW');

-- Review decisions are a closed set; results are immutable bar staleness and review class.
alter table review add constraint review_decision_domain
  check (decision in ('approve', 'reject', 'request_changes'));
create trigger rule_result_immutable before update on rule_result
  for each row execute function forbid_column_change(
    'firm_id', 'revision_id', 'rule_id', 'edition', 'result', 'inputs', 'citation');

-- Clients read only released artifacts, and never share-link tokens.
drop policy artifact_firm_select on artifact;
create policy artifact_released_select on artifact for select
  using (firm_id = current_firm_id() and released);
revoke all on ledger_link from authenticated;

-- Freezing a revision takes a share lock on its project row, so a project fact change (edition,
-- state, climate zone, class) cannot slip in beside the freeze in either order.
create function revision_freeze_locks_project() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if old.frozen_at is null and new.frozen_at is not null then
    perform 1 from public.project p where p.id = new.project_id and p.firm_id = new.firm_id for share;
  end if;
  return new;
end $$;
create trigger revision_freeze_lock_project before update of frozen_at on revision
  for each row execute function revision_freeze_locks_project();
