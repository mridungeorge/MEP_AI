-- Phase 5 consolidated review, round 1.

-- (A1) An evidence link can be created only by evidence_add_space, never attached to an existing space and never pointing at another firm's row.
alter table extraction add constraint extraction_id_firm_unique unique (id, firm_id);
alter table space drop constraint space_evidence_area_id_fkey, drop constraint space_evidence_void_id_fkey;
alter table space
  add constraint space_evidence_area_fk foreign key (evidence_area_id, firm_id) references extraction (id, firm_id),
  add constraint space_evidence_void_fk foreign key (evidence_void_id, firm_id) references extraction (id, firm_id);

create or replace function space_evidence_lock() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
  -- a link (or its page, source, unit) can never be ADDED to a space that has none, by any role
  if (old.evidence_area_id is null and new.evidence_area_id is not null) or (old.evidence_void_id is null and new.evidence_void_id is not null)
     or (old.evidence_area_id is null and old.evidence_void_id is null
         and (new.evidence_source_sha256 is not null or new.evidence_page is not null
              or new.evidence_area_unit is not null or new.evidence_void_unit is not null)) then
    raise exception 'an evidence link is made only when a space is added from a drawing reading';
  end if;
  if old.evidence_area_id is not null or old.evidence_void_id is not null then
    if new.evidence_area_id is distinct from old.evidence_area_id or new.evidence_void_id is distinct from old.evidence_void_id
       or new.evidence_source_sha256 is distinct from old.evidence_source_sha256 or new.evidence_page is distinct from old.evidence_page
       or new.evidence_area_unit is distinct from old.evidence_area_unit or new.evidence_void_unit is distinct from old.evidence_void_unit then
      raise exception 'the evidence link of a space cannot be changed';
    end if;
  end if;
  if old.evidence_area_id is not null and (new.area_m2_value is distinct from old.area_m2_value or new.area_m2_unit is distinct from old.area_m2_unit
       or (new.area_m2_provenance is distinct from old.area_m2_provenance and new.area_m2_provenance is distinct from 'engineer_confirmed')) then
    raise exception 'the area of this space was read from a drawing: it cannot be edited or relabelled (delete the space and add it again)';
  end if;
  if old.evidence_void_id is not null and (new.ceiling_void_mm_value is distinct from old.ceiling_void_mm_value
       or new.ceiling_void_mm_unit is distinct from old.ceiling_void_mm_unit
       or (new.ceiling_void_mm_provenance is distinct from old.ceiling_void_mm_provenance
           and new.ceiling_void_mm_provenance is distinct from 'engineer_confirmed')) then
    raise exception 'the ceiling void of this space was read from a drawing: it cannot be edited or relabelled';
  end if;
  return new;
end $$;

-- (A, nits) evidence_add_space: look names/uses/storeys up among PDF space rows only, and ledger what was stored.
create or replace function evidence_add_space(p_revision uuid, p_source_sha256 text, p_entity_key text, p_area_unit text, p_void_unit text default null)
returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare
  v_firm uuid := public.current_firm_id(); a record; v record; nm text; us text; st text; fa numeric; fv numeric; sid uuid; pg int;
  area_m2 numeric; void_mm numeric;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer adds a space from evidence' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = v_firm and frozen_at is null) then
    raise exception 'no such open revision in your firm' using errcode = '42501';
  end if;
  fa := case p_area_unit when 'm^2' then 1 when 'ft^2' then 0.09290304 when 'mm^2' then 0.000001 end;
  if fa is null then raise exception 'say which area unit the drawing uses: m^2, ft^2 or mm^2'; end if;
  select * into a from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'area';
  if a.id is null then raise exception 'that candidate has no area read from the drawing'; end if;
  select value_text into nm from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'name' limit 1;
  if nm is null then raise exception 'that candidate has no name read from the drawing'; end if;
  select value_text into us from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'use' limit 1;
  select value_text into st from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'storey' limit 1;
  select * into v from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'ceiling_void';
  if v.id is not null then
    fv := case p_void_unit when 'mm' then 1 when 'm' then 1000 when 'in' then 25.4 when 'ft' then 304.8 end;
    if fv is null then raise exception 'say which unit the ceiling void uses: mm, m, in or ft'; end if;
  end if;
  pg := substring(p_entity_key from '^p([0-9]+)-')::int;
  area_m2 := round(a.value_number * fa, 6);
  void_mm := case when v.id is null then null else round(v.value_number * fv, 6) end;
  insert into public.space (firm_id, revision_id, name, use, storey, area_m2_value, area_m2_provenance,
                            ceiling_void_mm_value, ceiling_void_mm_provenance,
                            evidence_area_id, evidence_void_id, evidence_source_sha256, evidence_page, evidence_area_unit, evidence_void_unit)
    values (v_firm, p_revision, nm, us, st, area_m2, 'extracted', void_mm,
            case when v.id is null then null else 'extracted'::provenance end,
            a.id, v.id, p_source_sha256, pg, p_area_unit, case when v.id is null then null else p_void_unit end)
    returning id into sid;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (v_firm, p_revision, 'evidence_space_added', jsonb_build_object('space', sid, 'by', auth.uid(), 'source_sha256', p_source_sha256,
            'page', pg, 'entity_key', p_entity_key, 'area_extraction', a.id, 'area_read', a.value_number, 'area_unit', p_area_unit,
            'area_m2_stored', area_m2, 'void_extraction', v.id, 'void_read', v.value_number, 'void_unit', p_void_unit, 'void_mm_stored', void_mm));
  return sid;
end $$;

-- (A2) confirming a version that is already confirmed is not an error; the note must belong to this revision and firm.
create or replace function spec_card_confirm(p_revision uuid, p_skill text, p_sha256 text, p_note uuid default null) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare sid uuid;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer confirms a spec card' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = public.current_firm_id() and frozen_at is null) then
    raise exception 'no such open revision in your firm' using errcode = '42501';
  end if;
  if p_note is not null and not exists (select 1 from public.agent_note where id = p_note and revision_id = p_revision and firm_id = public.current_firm_id()) then
    p_note := null;                                          -- a note of another revision or firm is simply not linked
  end if;
  select id into sid from public.spec_confirmation where revision_id = p_revision and skill = p_skill and spec_sha256 = p_sha256;
  if sid is null then
    insert into public.spec_confirmation (firm_id, revision_id, skill, spec_sha256, note_id, confirmed_by)
      values (public.current_firm_id(), p_revision, p_skill, p_sha256, p_note, auth.uid())
      on conflict (revision_id, skill, spec_sha256) do nothing
      returning id into sid;
    if sid is null then
      select id into sid from public.spec_confirmation where revision_id = p_revision and skill = p_skill and spec_sha256 = p_sha256;
    end if;
  end if;
  return sid;
end $$;

-- (B) a vision job that finds the readers busy waits before it is taken again.
alter table vision_job add column not_before timestamptz;
