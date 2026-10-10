-- Phase 7.3-7.5: the mechanical services schedule (proposed ducts, fittings and terminals), the ceiling-void clearance setting, and other disciplines' models for
-- clash-lite. None of this is compliance: it is geometry and counting the designer can check, and clash results are warnings only.

alter table firm add column void_clearance_mm numeric not null default 50 check (void_clearance_mm >= 0 and void_clearance_mm <= 1000);

create table duct_run (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  kind text not null check (kind in ('duct', 'fitting', 'terminal')),
  tag text not null check (length(tag) between 1 and 60),
  system_tag text check (system_tag is null or length(system_tag) <= 60),
  space_id uuid,
  shape text check (shape is null or shape in ('rect', 'round')),
  width_mm numeric check (width_mm is null or (width_mm > 0 and width_mm <= 10000)),
  depth_mm numeric check (depth_mm is null or (depth_mm > 0 and depth_mm <= 10000)),
  diameter_mm numeric check (diameter_mm is null or (diameter_mm > 0 and diameter_mm <= 10000)),
  length_m numeric check (length_m is null or (length_m > 0 and length_m <= 1000)),
  insulation_mm numeric not null default 0 check (insulation_mm >= 0 and insulation_mm <= 500),
  fitting_type text check (fitting_type is null or length(fitting_type) <= 60),
  quantity int not null default 1 check (quantity between 1 and 10000),
  airflow_ls numeric check (airflow_ls is null or (airflow_ls >= 0 and airflow_ls <= 1000000)),
  x0 numeric, y0 numeric, z0 numeric, x1 numeric, y1 numeric, z1 numeric,
  created_by uuid not null,
  created_at timestamptz not null default now(),
  constraint duct_run_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint duct_run_space_fk foreign key (space_id) references space (id) on delete set null,
  constraint duct_run_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  check (kind <> 'duct' or (shape is not null and length_m is not null and ((shape = 'rect' and width_mm is not null and depth_mm is not null)
                                                                         or (shape = 'round' and diameter_mm is not null)))),
  check (kind <> 'fitting' or fitting_type is not null),
  check (x0 is null or (abs(x0) < 1e9 and abs(y0) < 1e9 and abs(z0) < 1e9 and abs(x1) < 1e9 and abs(y1) < 1e9 and abs(z1) < 1e9)),
  check ((x0 is null) = (x1 is null) and (y0 is null) = (y1 is null) and (z0 is null) = (z1 is null) and (x0 is null) = (y0 is null) and (y0 is null) = (z0 is null))
);
create index duct_run_revision on duct_run (revision_id, kind, tag);
alter table duct_run enable row level security;
create policy duct_run_select on duct_run for select using (firm_id = current_firm_id());
revoke all on duct_run from anon;
revoke insert, update, delete, truncate, trigger, references on duct_run from authenticated;
create trigger duct_run_frozen before insert or update or delete on duct_run for each row execute function reject_if_revision_frozen();

-- other disciplines' models (electrical, hydraulic, fire) reduced to bounding boxes
create table clash_model (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  discipline text not null check (discipline in ('electrical', 'hydraulic', 'fire')),
  file_name text not null check (length(file_name) <= 200),
  file_sha256 text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
  element_count int not null check (element_count >= 0),
  skipped int not null default 0,
  problems jsonb not null default '[]',
  created_by uuid not null,
  created_at timestamptz not null default now(),
  constraint clash_model_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint clash_model_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  unique (revision_id, file_sha256)
);
create table clash_element (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  model_id uuid not null references clash_model (id) on delete cascade,
  revision_id uuid not null,
  guid text not null check (length(guid) <= 64),
  ifc_class text not null check (length(ifc_class) <= 60),
  name text check (name is null or length(name) <= 200),
  min_x numeric not null, min_y numeric not null, min_z numeric not null,
  max_x numeric not null, max_y numeric not null, max_z numeric not null,
  check (max_x >= min_x and max_y >= min_y and max_z >= min_z)
);
create index clash_element_revision on clash_element (revision_id);
alter table clash_model enable row level security;
alter table clash_element enable row level security;
create policy clash_model_select on clash_model for select using (firm_id = current_firm_id());
create policy clash_element_select on clash_element for select using (firm_id = current_firm_id());
revoke all on clash_model, clash_element from anon;
revoke insert, update, delete, truncate, trigger, references on clash_model, clash_element from authenticated;
create trigger clash_model_frozen before insert on clash_model for each row execute function reject_if_revision_frozen();
create trigger clash_element_frozen before insert on clash_element for each row execute function reject_if_revision_frozen();

-- the firm administrator sets the clearance (mm) kept around ducts in the ceiling void and in clash-lite
drop function admin_set_settings(text, text, int, numeric);
create function admin_set_settings(p_name text, p_signer_mode text, p_sample_size int, p_near_miss numeric, p_void_clearance numeric default null) returns void
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if p_signer_mode not in ('strict', 'small_firm') then raise exception 'signer mode is strict or small_firm'; end if;
  if p_sample_size is null or p_sample_size < 1 or p_sample_size > 1000 then raise exception 'the spot-check sample size is 1 to 1000'; end if;
  if p_void_clearance is not null and (p_void_clearance < 0 or p_void_clearance > 1000) then raise exception 'the clearance is 0 to 1000 mm'; end if;
  update public.firm set name = coalesce(nullif(btrim(p_name), ''), name), signer_mode = p_signer_mode, sample_size = p_sample_size,
         near_miss_default = p_near_miss, void_clearance_mm = coalesce(p_void_clearance, void_clearance_mm) where id = f;
end $$;
revoke all on function admin_set_settings(text, text, int, numeric, numeric) from public, anon;
grant execute on function admin_set_settings(text, text, int, numeric, numeric) to authenticated;
create or replace function firm_setting_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if new.signer_mode is distinct from old.signer_mode then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_signer_mode_changed', jsonb_build_object('from', old.signer_mode, 'to', new.signer_mode, 'by', auth.uid()));
  end if;
  if (new.sample_size, new.near_miss_default, new.name, new.void_clearance_mm) is distinct from (old.sample_size, old.near_miss_default, old.name, old.void_clearance_mm) then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_settings_changed', jsonb_build_object('by', auth.uid(),
        'sample_size', jsonb_build_array(old.sample_size, new.sample_size),
        'near_miss_default', jsonb_build_array(old.near_miss_default, new.near_miss_default),
        'void_clearance_mm', jsonb_build_array(old.void_clearance_mm, new.void_clearance_mm),
        'name', jsonb_build_array(old.name, new.name)));
  end if;
  return new;
end $$;
