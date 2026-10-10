-- Phase 5: a space made from evidence stays EXTRACTED. It links to the evidence rows and the page, records the unit the designer declared
-- (nothing is assumed), needs Gate 1 like every extracted value, and its evidence-derived values cannot be edited or relabelled as hand-entered.
alter table space
  add column evidence_area_id uuid references extraction (id),
  add column evidence_void_id uuid references extraction (id),
  add column evidence_source_sha256 text,
  add column evidence_page int check (evidence_page is null or evidence_page > 0),
  add column evidence_area_unit text,
  add column evidence_void_unit text;
create unique index space_evidence_area_once on space (revision_id, evidence_area_id) where evidence_area_id is not null;

create function space_evidence_lock() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
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
create trigger space_evidence_lock before update on space for each row execute function space_evidence_lock();

create function space_evidence_insert_guard() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
  -- an evidence link exists only with provenance 'extracted' and a matching extraction row of the same revision and firm
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
create trigger space_evidence_insert before insert on space for each row execute function space_evidence_insert_guard();

create function evidence_add_space(p_revision uuid, p_source_sha256 text, p_entity_key text, p_area_unit text, p_void_unit text default null)
returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare
  v_firm uuid := public.current_firm_id(); a record; v record; nm text; us text; st text; fa numeric; fv numeric; sid uuid; pg int;
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
    and entity_key = p_entity_key and field = 'name';
  if nm is null then raise exception 'that candidate has no name read from the drawing'; end if;
  select value_text into us from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and entity_key = p_entity_key and field = 'use';
  select value_text into st from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and entity_key = p_entity_key and field = 'storey';
  select * into v from public.extraction where revision_id = p_revision and firm_id = v_firm and source_sha256 = p_source_sha256
    and source_kind = 'pdf' and entity_kind = 'space' and entity_key = p_entity_key and field = 'ceiling_void';
  if v.id is not null then
    fv := case p_void_unit when 'mm' then 1 when 'm' then 1000 when 'in' then 25.4 when 'ft' then 304.8 end;
    if fv is null then raise exception 'say which unit the ceiling void uses: mm, m, in or ft'; end if;
  end if;
  pg := substring(p_entity_key from '^p([0-9]+)-')::int;
  insert into public.space (firm_id, revision_id, name, use, storey, area_m2_value, area_m2_provenance,
                            ceiling_void_mm_value, ceiling_void_mm_provenance,
                            evidence_area_id, evidence_void_id, evidence_source_sha256, evidence_page, evidence_area_unit, evidence_void_unit)
    values (v_firm, p_revision, nm, us, st, round(a.value_number * fa, 6), 'extracted',
            case when v.id is null then null else round(v.value_number * fv, 6) end,
            case when v.id is null then null else 'extracted'::provenance end,
            a.id, v.id, p_source_sha256, pg, p_area_unit, case when v.id is null then null else p_void_unit end)
    returning id into sid;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (v_firm, p_revision, 'evidence_space_added', jsonb_build_object('space', sid, 'by', auth.uid(), 'source_sha256', p_source_sha256,
            'page', pg, 'entity_key', p_entity_key, 'area_unit', p_area_unit));
  return sid;
end $$;
revoke all on function evidence_add_space(uuid, text, text, text, text) from public, anon;
grant execute on function evidence_add_space(uuid, text, text, text, text) to authenticated;
