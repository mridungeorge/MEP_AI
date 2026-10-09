-- Phase 3 adversarial review, round 2 (migrations 0010 and 0011 are not edited).

-- 3/4/7. A canonical fingerprint: rows as jsonb (NULLs and types stay distinct, nothing needs escaping), provenance included, systems with
--        no inputs included, scoped to the caller's firm when called by a client.
create or replace function live_inputs_hash(p_revision uuid) returns text language plpgsql stable security definer
  set search_path = public, pg_temp as
$$
declare v_firm uuid; v_project uuid; doc jsonb;
begin
  select r.firm_id, r.project_id into v_firm, v_project from public.revision r where r.id = p_revision;
  if v_firm is null then return null; end if;
  if current_setting('role', true) in ('anon', 'authenticated') and v_firm is distinct from public.current_firm_id() then
    return null;
  end if;
  doc := jsonb_build_array(
    (select coalesce(to_jsonb(p) - 'id' - 'firm_id' - 'address' - 'confirmed_at', 'null') from (
        select state, climate_zone, building_class, ncc_edition, approval_date, confirmed_by is not null as confirmed
          from public.project where id = v_project and firm_id = v_firm) p),
    (select coalesce(jsonb_agg(to_jsonb(b) order by b.position), '[]') from (
        select position, building_class, storeys, area_m2_value, area_m2_provenance, confirmed_by is not null as confirmed
          from public.building_part where project_id = v_project and firm_id = v_firm) b),
    (select coalesce(jsonb_agg(to_jsonb(s) order by s.id), '[]') from (
        select id, name, use, storey, area_m2_value, area_m2_provenance, ceiling_void_mm_value, ceiling_void_mm_provenance, ifc_guid,
               confirmed_by is not null as confirmed from public.space where revision_id = p_revision and firm_id = v_firm) s),
    (select coalesce(jsonb_agg(to_jsonb(y) order by y.tag), '[]') from (
        select coalesce(s.tag, s.id::text) as tag, s.type,
               (select coalesce(jsonb_agg(to_jsonb(i) order by i.name), '[]') from (
                  select name, value_number, value_text, value_bool, unit, provenance::text as provenance,
                         confirmed_by is not null as confirmed from public.system_input
                   where system_id = s.id and firm_id = v_firm) i) as inputs
          from public.system s where s.revision_id = p_revision and s.firm_id = v_firm) y));
  return encode(sha256(convert_to(doc::text, 'UTF8')), 'hex');
end $$;

-- 2/5. The guard locks the revision row (a freeze in flight is waited for) and allows ONE change on a frozen revision: review_class.
create or replace function rule_result_frozen_guard() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid; locked boolean;
begin
  for rev in select distinct x from unnest(array[old.revision_id, new.revision_id]) x where x is not null loop
    select frozen_at is not null into locked from public.revision where id = rev for share;
    if locked then
      if tg_op = 'UPDATE' and old.revision_id = new.revision_id
         and (to_jsonb(new) - 'review_class') = (to_jsonb(old) - 'review_class') then
        continue;
      end if;
      raise exception 'revision is frozen: its results cannot be replaced or removed';
    end if;
  end loop;
  return coalesce(new, old);
end $$;

-- 2. Freezing locks the revision first, then checks the fingerprint, so an edit cannot slip in between the check and the freeze.
create or replace function freeze_revision(p_revision uuid) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare head_seq bigint; head_hash text; ran text[]; parent uuid;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer freezes a revision' using errcode = '42501';
  end if;
  select parent_revision_id into parent from public.revision
    where id = p_revision and firm_id = public.current_firm_id() for update;
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

-- 6. The diff is confirmed only through the API, which checks the hash the designer saw and that every changed space is confirmed.
--    The client-callable function is withdrawn; the service-side one takes the (verified) user id.
revoke execute on function confirm_revision_diff(uuid, text) from authenticated;
create function confirm_revision_diff_as(p_user uuid, p_revision uuid, p_hash text) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare me public.app_user; parent uuid; frozen timestamptz;
begin
  select * into me from public.app_user where id = p_user;
  if me.id is null or me.role is distinct from 'designer' then
    raise exception 'only a designer confirms a revision diff' using errcode = '42501';
  end if;
  if p_hash !~ '^[0-9a-f]{64}$' then raise exception 'bad diff hash'; end if;
  select parent_revision_id, frozen_at into parent, frozen from public.revision
    where id = p_revision and firm_id = me.firm_id for update;
  if not found then raise exception 'revision not found in your firm' using errcode = '42501'; end if;
  if parent is null then raise exception 'this revision has no parent: there is no diff to confirm'; end if;
  if frozen is not null then raise exception 'revision is frozen'; end if;
  insert into public.revision_diff (firm_id, revision_id, parent_revision_id, diff_hash, confirmed_by)
    values (me.firm_id, p_revision, parent, p_hash, me.id)
    on conflict (revision_id) do update set diff_hash = excluded.diff_hash, confirmed_by = excluded.confirmed_by,
                                            confirmed_at = now();
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (me.firm_id, p_revision, 'diff_confirm',
            jsonb_build_object('parent', parent, 'diff_hash', p_hash, 'confirmed_by', me.id, 'role', 'designer'));
end $$;
revoke all on function confirm_revision_diff_as(uuid, uuid, text) from public, anon, authenticated;

-- 8. created_from_sha256 is set once by the service and never by a client.
create or replace function revision_parent_service_only() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if (new.parent_revision_id is not null or new.created_from_sha256 is not null)
     and current_setting('role', true) in ('anon', 'authenticated') then
    raise exception 'child revisions are made by the upload service, not by a client' using errcode = '42501';
  end if;
  return new;
end $$;
create trigger revision_created_from_immutable before update on revision
  for each row execute function forbid_column_change('created_from_sha256');
