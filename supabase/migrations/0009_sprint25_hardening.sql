-- Sprint 2.5 review hardening, as a NEW migration (0006 and 0007 were already applied somewhere and are not edited).

-- 1. Any change to a building part that the engine reads (class, storeys, area) withdraws the confirmed part assignments,
--    not only an insert or a change of position (0006).
create or replace function building_part_shift_withdraws() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if tg_op = 'INSERT' or (new.position, new.building_class, new.storeys, new.area_m2_value)
                         is distinct from (old.position, old.building_class, old.storeys, old.area_m2_value) then
    update public.building_part set confirmed_by = null, confirmed_at = null
      where project_id = new.project_id and firm_id = new.firm_id and id <> new.id and confirmed_by is not null;
    update public.system_input si set confirmed_by = null, confirmed_at = null, provenance = 'default'
      from public.system s join public.revision r on r.id = s.revision_id and r.firm_id = s.firm_id
      where si.system_id = s.id and si.firm_id = new.firm_id and r.project_id = new.project_id
        and si.name = 'building_part' and si.confirmed_by is not null;
  end if;
  return new;
end $$;

-- 2. The uploads bucket takes only octet-stream (the API sends that; no HTML can be served back from the storage origin), and
--    nothing can be added to a frozen revision's folder (0007).
update storage.buckets set allowed_mime_types = array['application/octet-stream'] where id = 'uploads';
drop policy uploads_designer_insert on storage.objects;
create policy uploads_designer_insert on storage.objects for insert to authenticated
  with check (
    bucket_id = 'uploads'
    and array_length(string_to_array(name, '/'), 1) = 3
    and (string_to_array(name, '/'))[1] = public.current_firm_id()::text
    and public.current_user_role() = 'designer'
    and exists (select 1 from public.revision r
                 where r.id::text = (string_to_array(name, '/'))[2] and r.firm_id = public.current_firm_id()
                   and r.frozen_at is null)
    and (string_to_array(name, '/'))[3] ~ '^[0-9a-f]{64}\.(ifc|dxf)$');
