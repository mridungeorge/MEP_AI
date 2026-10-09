-- Sprint 2 finish: systems have a schedule tag, and every Gate 1 confirmation is ledgered in the same transaction.

-- 1. A system is identified in the schedule by its tag (unique within a revision). The tag is a label, not provenance.
alter table system add column tag text check (tag is null or (length(tag) between 1 and 64 and tag = btrim(tag)));
create unique index system_revision_tag_uq on system (revision_id, tag) where tag is not null;

-- 2. gate1_confirm records WHO confirmed WHAT in ledger_event, atomically with the confirmation: if the ledger row
--    cannot be written the confirmation rolls back. p_revision (optional) ties the event to a revision of the caller's
--    firm; a revision of another firm is refused. Same checks as 0004: designer only, own firm only.
drop function gate1_confirm(text, uuid[]);
create function gate1_confirm(p_kind text, p_ids uuid[], p_revision uuid default null) returns int
  language plpgsql security definer set search_path = public, pg_temp as
$$
declare n int; wanted int;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer confirms values at gate 1' using errcode = '42501';
  end if;
  if p_ids is null or cardinality(p_ids) = 0 then
    raise exception 'no rows to confirm';
  end if;
  if p_revision is not null and not exists (
       select 1 from public.revision where id = p_revision and firm_id = public.current_firm_id()) then
    raise exception 'revision not found in your firm' using errcode = '42501';
  end if;
  select count(distinct x) into wanted from unnest(p_ids) x;
  if p_kind = 'space' then
    update public.space set
      area_m2_provenance = case when area_m2_value is null then area_m2_provenance else 'engineer_confirmed' end,
      ceiling_void_mm_provenance = case when ceiling_void_mm_value is null then ceiling_void_mm_provenance
                                        else 'engineer_confirmed' end,
      confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id()
        and (p_revision is null or revision_id = p_revision);
  elsif p_kind = 'project' then
    if exists (select 1 from public.project where id = any(p_ids) and firm_id = public.current_firm_id()
                 and (state is null or ncc_edition is null or climate_zone is null or approval_date is null)) then
      raise exception 'state, NCC edition, climate zone and approval date must all be set before they are confirmed';
    end if;
    update public.project set confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id()
        and (p_revision is null or id = (select project_id from public.revision where id = p_revision));
  elsif p_kind = 'building_part' then
    update public.building_part set
      area_m2_provenance = case when area_m2_value is null then area_m2_provenance else 'engineer_confirmed' end,
      confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id()
        and (p_revision is null or project_id = (select project_id from public.revision where id = p_revision));
  elsif p_kind = 'system_input' then
    update public.system_input set provenance = 'engineer_confirmed', confirmed_by = auth.uid(), confirmed_at = now()
      where id = any(p_ids) and firm_id = public.current_firm_id()
        and (p_revision is null or exists (select 1 from public.system s
                                            where s.id = system_input.system_id and s.revision_id = p_revision));
  else
    raise exception 'unknown kind %', p_kind;
  end if;
  get diagnostics n = row_count;
  if n <> wanted then
    raise exception 'some rows were not found in your firm and revision';
  end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'gate1_confirm',
            jsonb_build_object('kind', p_kind, 'ids', to_jsonb(p_ids), 'confirmed_by', auth.uid(), 'role', 'designer'));
  return n;
end $$;
revoke all on function gate1_confirm(text, uuid[], uuid) from public, anon;
grant execute on function gate1_confirm(text, uuid[], uuid) to authenticated;

-- 3. An edit may keep a provenance the row already has (an IFC value the designer did not touch stays 'extracted'), but a
--    client still cannot WRITE any provenance other than 'default'. 0003 rejected every non-default provenance in the new
--    row, which forced the API to relabel untouched extracted values as 'default'.
create or replace function client_provenance_default_only() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$
declare k text; v text;
begin
  if current_user in ('anon', 'authenticated') then
    for k, v in select key, value from jsonb_each_text(to_jsonb(new)) where key like '%\_provenance' loop
      if v is not null and v <> 'default' then
        if tg_op = 'INSERT' or v is distinct from (to_jsonb(old) ->> k) then
          raise exception '% may only be set to default by a client (set by the API)', k;
        end if;
        -- a label may be kept only while the value and unit it describes are unchanged (else an edit would inherit
        -- the label of the value it replaced): '<x>_provenance' describes '<x>_value' and '<x>_unit'
        if (to_jsonb(new) ->> (left(k, -11) || '_value')) is distinct from (to_jsonb(old) ->> (left(k, -11) || '_value'))
           or (to_jsonb(new) ->> (left(k, -11) || '_unit')) is distinct from (to_jsonb(old) ->> (left(k, -11) || '_unit')) then
          raise exception '% cannot keep its label when the value changes: set it to default', k;
        end if;
      end if;
    end loop;
  end if;
  return new;
end $$;
