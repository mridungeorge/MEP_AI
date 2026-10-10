-- Phase 8.1: duct sizing settings are a firm choice (friction rate, roughness, size increment, velocity limits...). Stored as one validated JSON object; the API
-- validates keys and ranges (mep/sizing.py). There are no default velocity limits: an unset limit means "NO LIMIT SET", never a made-up number.
alter table firm add column sizing_settings jsonb not null default '{}' check (jsonb_typeof(sizing_settings) = 'object' and length(sizing_settings::text) < 4000);

create function admin_set_sizing(p_settings jsonb) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if p_settings is null or jsonb_typeof(p_settings) <> 'object' then raise exception 'sizing settings are a JSON object'; end if;
  update public.firm set sizing_settings = p_settings where id = f;
end $$;
revoke all on function admin_set_sizing(jsonb) from public, anon;
grant execute on function admin_set_sizing(jsonb) to authenticated;

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
  if new.sizing_settings is distinct from old.sizing_settings then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_sizing_settings_changed', jsonb_build_object('by', auth.uid(), 'from', old.sizing_settings, 'to', new.sizing_settings));
  end if;
  return new;
end $$;
