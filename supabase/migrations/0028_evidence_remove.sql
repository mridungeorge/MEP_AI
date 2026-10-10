-- Phase 5 review round 2: a space made from evidence can be removed (and then added again, e.g. with the right unit); a crafted insert cannot leave
-- half an evidence link behind.

-- (2) no evidence_* value at all unless there is a link; the lock compares what CHANGED, not what is non-null.
create or replace function space_evidence_insert_guard() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
  if new.evidence_area_id is null and new.evidence_void_id is null
     and (new.evidence_source_sha256 is not null or new.evidence_page is not null
          or new.evidence_area_unit is not null or new.evidence_void_unit is not null) then
    raise exception 'evidence columns are set only together with an evidence link';
  end if;
  if new.evidence_area_id is not null then
    if new.area_m2_provenance is distinct from 'extracted' or not exists (
         select 1 from extraction e where e.id = new.evidence_area_id and e.revision_id = new.revision_id and e.firm_id = new.firm_id
           and e.field = 'area') then
      raise exception 'an evidence-linked area must be extracted and point at its extraction row';
    end if;
  end if;
  if new.evidence_void_id is not null then
    if new.ceiling_void_mm_provenance is distinct from 'extracted' or not exists (
         select 1 from extraction e where e.id = new.evidence_void_id and e.revision_id = new.revision_id and e.firm_id = new.firm_id
           and e.field = 'ceiling_void') then
      raise exception 'an evidence-linked ceiling void must be extracted and point at its extraction row';
    end if;
  end if;
  return new;
end $$;

create or replace function space_evidence_lock() returns trigger language plpgsql set search_path = public, pg_temp as
$$
declare had_link boolean := old.evidence_area_id is not null or old.evidence_void_id is not null;
begin
  if not had_link then
    if new.evidence_area_id is not null or new.evidence_void_id is not null
       or new.evidence_source_sha256 is distinct from old.evidence_source_sha256 or new.evidence_page is distinct from old.evidence_page
       or new.evidence_area_unit is distinct from old.evidence_area_unit or new.evidence_void_unit is distinct from old.evidence_void_unit then
      raise exception 'an evidence link is made only when a space is added from a drawing reading';
    end if;
    return new;
  end if;
  if new.evidence_area_id is distinct from old.evidence_area_id or new.evidence_void_id is distinct from old.evidence_void_id
     or new.evidence_source_sha256 is distinct from old.evidence_source_sha256 or new.evidence_page is distinct from old.evidence_page
     or new.evidence_area_unit is distinct from old.evidence_area_unit or new.evidence_void_unit is distinct from old.evidence_void_unit then
    raise exception 'the evidence link of a space cannot be changed';
  end if;
  if old.evidence_area_id is not null and (new.area_m2_value is distinct from old.area_m2_value or new.area_m2_unit is distinct from old.area_m2_unit
       or (new.area_m2_provenance is distinct from old.area_m2_provenance and new.area_m2_provenance is distinct from 'engineer_confirmed')) then
    raise exception 'the area of this space was read from a drawing: it cannot be edited or relabelled (remove the space and add it again)';
  end if;
  if old.evidence_void_id is not null and (new.ceiling_void_mm_value is distinct from old.ceiling_void_mm_value
       or new.ceiling_void_mm_unit is distinct from old.ceiling_void_mm_unit
       or (new.ceiling_void_mm_provenance is distinct from old.ceiling_void_mm_provenance
           and new.ceiling_void_mm_provenance is distinct from 'engineer_confirmed')) then
    raise exception 'the ceiling void of this space was read from a drawing: it cannot be edited or relabelled';
  end if;
  return new;
end $$;

-- (1) removing an evidence space: designer, open revision, ledgered. Only spaces made from evidence (a typed or IFC space is not removed this way).
create function evidence_remove_space(p_space uuid) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare s record;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer removes a space' using errcode = '42501';
  end if;
  select * into s from public.space where id = p_space and firm_id = public.current_firm_id();
  if s.id is null or (s.evidence_area_id is null and s.evidence_void_id is null) then
    raise exception 'no such space made from a drawing reading in your firm' using errcode = '42501';
  end if;
  if exists (select 1 from public.revision where id = s.revision_id and frozen_at is not null) then
    raise exception 'the revision is frozen: nothing about it can change' using errcode = '42501';
  end if;
  delete from public.space where id = p_space;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (s.firm_id, s.revision_id, 'evidence_space_removed', jsonb_build_object('space', p_space, 'by', auth.uid(), 'name', s.name,
            'area_m2_stored', s.area_m2_value, 'declared_unit', s.evidence_area_unit, 'was_confirmed', s.confirmed_by is not null));
end $$;
revoke all on function evidence_remove_space(uuid) from public, anon;
grant execute on function evidence_remove_space(uuid) to authenticated;

-- (3) vision_job: a late finisher cannot overwrite a newer run is handled in the API (`where status = 'running'`).
