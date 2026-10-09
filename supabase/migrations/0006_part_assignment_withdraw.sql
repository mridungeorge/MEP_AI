-- Sprint 2.5: a system names the building part it serves (schedule input 'building_part', a part position).
-- The positions are what the input refers to, so any change to the project's parts (insert, delete, reorder) withdraws the
-- confirmation of every system's part assignment in that project: the designer confirms them again against the new parts.

create or replace function building_part_delete_withdraws() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  update public.building_part set confirmed_by = null, confirmed_at = null
    where project_id = old.project_id and firm_id = old.firm_id and confirmed_by is not null;
  update public.system_input si set confirmed_by = null, confirmed_at = null, provenance = 'default'
    from public.system s join public.revision r on r.id = s.revision_id and r.firm_id = s.firm_id
    where si.system_id = s.id and si.firm_id = old.firm_id and r.project_id = old.project_id
      and si.name = 'building_part' and si.confirmed_by is not null;
  return old;
end $$;

create or replace function building_part_shift_withdraws() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  -- an inserted part or a changed position shifts the indices subjects refer to: confirm the list again
  if tg_op = 'INSERT' or new.position is distinct from old.position then
    update public.building_part set confirmed_by = null, confirmed_at = null
      where project_id = new.project_id and firm_id = new.firm_id and id <> new.id and confirmed_by is not null;
    update public.system_input si set confirmed_by = null, confirmed_at = null, provenance = 'default'
      from public.system s join public.revision r on r.id = s.revision_id and r.firm_id = s.firm_id
      where si.system_id = s.id and si.firm_id = new.firm_id and r.project_id = new.project_id
        and si.name = 'building_part' and si.confirmed_by is not null;
  end if;
  return new;
end $$;
