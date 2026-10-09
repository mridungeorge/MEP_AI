-- Phase 3 adversarial review, round 1 (as a new migration: 0008-0010 are not edited).

-- B1. A frozen revision's stored results are never replaced, added to or removed. (Classification, which only sets review_class
--     after the freeze, stays possible until Gate 2 is signed: 0010.)
create function rule_result_frozen_guard() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid := coalesce(new.revision_id, old.revision_id);
begin
  if exists (select 1 from public.revision where id = rev and frozen_at is not null) then
    if tg_op = 'UPDATE' and (new.current, new.stale) is not distinct from (old.current, old.stale) then
      return new;
    end if;
    raise exception 'revision is frozen: its results cannot be replaced or removed';
  end if;
  return coalesce(new, old);
end $$;
create trigger rule_result_frozen_guard before insert or update or delete on rule_result
  for each row execute function rule_result_frozen_guard();

-- B2. A fingerprint of everything a run reads (project facts, building parts, spaces, systems, inputs, and whether each is confirmed).
--     Each run stores it; freezing requires that nothing has changed since the latest run.
create function live_inputs_hash(p_revision uuid) returns text language sql stable security definer
  set search_path = public, pg_temp as
$$
  select encode(sha256(convert_to(concat_ws('#',
    (select coalesce(string_agg(concat_ws('|', p.state, p.climate_zone, p.building_class, p.ncc_edition, p.approval_date,
                                          p.confirmed_by is not null), ';'), '')
       from public.project p join public.revision r on r.project_id = p.id and r.firm_id = p.firm_id where r.id = p_revision),
    (select coalesce(string_agg(concat_ws('|', b.position, b.building_class, b.storeys, b.area_m2_value, b.confirmed_by is not null),
                                ';' order by b.position), '')
       from public.building_part b join public.revision r on r.project_id = b.project_id and r.firm_id = b.firm_id where r.id = p_revision),
    (select coalesce(string_agg(concat_ws('|', sp.name, sp.use, sp.storey, sp.area_m2_value, sp.ceiling_void_mm_value,
                                          sp.ifc_guid, sp.confirmed_by is not null), ';' order by sp.id), '')
       from public.space sp where sp.revision_id = p_revision),
    (select coalesce(string_agg(concat_ws('|', coalesce(s.tag, s.id::text), s.type, i.name, i.value_number, i.value_text, i.value_bool,
                                          i.unit, i.confirmed_by is not null), ';' order by coalesce(s.tag, s.id::text), i.name), '')
       from public.system s join public.system_input i on i.system_id = s.id and i.firm_id = s.firm_id where s.revision_id = p_revision)
  ), 'UTF8')), 'hex')
$$;
revoke all on function live_inputs_hash(uuid) from public, anon;
grant execute on function live_inputs_hash(uuid) to authenticated;

alter table rule_result add column inputs_hash text;

-- Freezing: the latest run must have read exactly what is there now, and a child revision needs its diff confirmed.
create or replace function freeze_revision(p_revision uuid) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare head_seq bigint; head_hash text; ran text[]; parent uuid;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer freezes a revision' using errcode = '42501';
  end if;
  select parent_revision_id into parent from public.revision where id = p_revision and firm_id = public.current_firm_id();
  if not found then raise exception 'revision not found in your firm' using errcode = '42501'; end if;
  select array_agg(distinct inputs_hash) into ran from public.rule_result
    where revision_id = p_revision and firm_id = public.current_firm_id() and current;
  if ran is null then raise exception 'run the rules before freezing: there are no current results'; end if;
  if ran <> array[public.live_inputs_hash(p_revision)] then
    raise exception 'the inputs changed since the rules were last run: run them again before freezing';
  end if;
  if parent is not null and not exists (select 1 from public.revision_diff where revision_id = p_revision) then
    raise exception 'confirm the revision diff before freezing';
  end if;
  update public.revision set frozen_at = now(), status = 'frozen'
    where id = p_revision and firm_id = public.current_firm_id() and frozen_at is null;
  if not found then raise exception 'revision is already frozen'; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'revision_frozen',
            jsonb_build_object('frozen_by', auth.uid(), 'role', 'designer'));
  select seq, row_hash into head_seq, head_hash from public.ledger_event
    where firm_id = public.current_firm_id() order by seq desc limit 1;
  insert into public.signoff (firm_id, revision_id, gate, user_id, signer_role, anchor_seq, anchor_hash, statement)
    values (public.current_firm_id(), p_revision, 'gate1', auth.uid(), 'designer', head_seq, head_hash,
            jsonb_build_object('attests', 'inputs confirmed at Gate 1; revision frozen'));
end $$;

-- S5. Only the service (the upload that detects a frozen parent) makes a child revision; a client never inserts a parent link.
create function revision_parent_service_only() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if new.parent_revision_id is not null and current_setting('role', true) in ('anon', 'authenticated') then
    raise exception 'child revisions are made by the upload service, not by a client' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger revision_parent_service_only before insert on revision
  for each row execute function revision_parent_service_only();
revoke all on function rule_result_frozen_guard(), revision_parent_service_only() from public, anon, authenticated;

-- S4. One child per (parent, uploaded file): a retry or a double click cannot make sibling revisions.
alter table revision add column created_from_sha256 text;
create unique index revision_one_child_per_file on revision (parent_revision_id, created_from_sha256)
  where parent_revision_id is not null and created_from_sha256 is not null;
