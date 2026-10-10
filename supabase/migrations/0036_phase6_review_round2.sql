-- Phase 6 review round 2.

-- the site of a project does not change once it exists (one project cannot be reused for new buildings around the billing gate)
create function project_address_fixed() returns trigger language plpgsql set search_path = public, pg_temp as
$$ begin
  if new.address is distinct from old.address then raise exception 'the address of a project cannot be changed: start a new project'; end if;
  return new;
end $$;
create trigger project_address_fixed before update on project for each row execute function project_address_fixed();

-- one registration number is verified for one person at a time
create unique index registration_verified_once on registration (register, number) where status = 'verified';

-- renewal: the old verified row is superseded BEFORE the new one is verified (the index above allows one at a time)
create or replace function registration_decide(p_id uuid, p_verify boolean, p_note text) returns void language plpgsql security definer
set search_path = public, pg_temp as
$$
declare r public.registration; old text;
begin
  if not public.is_platform_admin() then raise exception 'platform administrators only' using errcode = '42501'; end if;
  select * into r from public.registration where id = p_id and status = 'submitted' for update;
  if r.id is null then raise exception 'no such registration awaiting a decision'; end if;
  if r.user_id = auth.uid() or r.submitted_by = auth.uid() or r.firm_id = public.current_firm_id() then
    raise exception 'you cannot decide a registration of your own firm, one you submitted, or one that is yours';
  end if;
  if p_verify and (p_note is null or length(btrim(p_note)) < 15) then
    raise exception 'say what you checked on the register, where and when (at least 15 characters)';
  end if;
  if p_verify then
    if exists (select 1 from public.registration where register = r.register and number = r.number and status = 'verified' and user_id <> r.user_id) then
      raise exception 'that number is already verified for another person: it cannot belong to two accounts';
    end if;
    update public.registration set status = 'superseded' where user_id = r.user_id and firm_id = r.firm_id and status = 'verified';
  end if;
  update public.registration set status = case when p_verify then 'verified' else 'rejected' end, decided_by = auth.uid(), decided_at = now(),
         decision_note = btrim(p_note) where id = p_id;
  if p_verify then
    select registration_no into old from public.app_user where id = r.user_id;
    perform set_config('mep.reg_path', 'verified', true);
    update public.app_user set registration_no = r.number where id = r.user_id and firm_id = r.firm_id;
  end if;
  insert into public.ledger_event (firm_id, kind, payload)
    values (r.firm_id, case when p_verify then 'registration_verified' else 'registration_rejected' end,
            jsonb_build_object('registration', p_id, 'user', r.user_id, 'number', r.number, 'register', r.register, 'state_scheme', r.state_scheme,
                               'previous', old, 'decided_by', auth.uid(), 'note_sha256', encode(sha256(convert_to(btrim(p_note), 'utf8')), 'hex')));
end $$;

-- concurrent project creations count one at a time
create or replace function project_create(p_address text, p_state text, p_edition text, p_climate_zone int, p_approval_date date) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id(); why text; pid uuid := gen_random_uuid(); rid uuid := gen_random_uuid();
begin
  if auth.uid() is null or f is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer starts a project' using errcode = '42501';
  end if;
  perform 1 from public.subscription where firm_id = f for update;
  why := public.billing_blocks_projects(f);
  if why is not null then raise exception '%', why using errcode = 'P0402'; end if;
  if length(btrim(coalesce(p_address, ''))) < 3 or length(p_address) > 200 then raise exception 'give the project address'; end if;
  insert into public.project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date)
    values (pid, f, btrim(p_address), p_state, p_climate_zone, p_edition, p_approval_date);
  insert into public.revision (id, firm_id, project_id, architect_rev) values (rid, f, pid, 'A');
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (f, rid, 'project_created', jsonb_build_object('project', pid, 'by', auth.uid(), 'state', p_state, 'edition', p_edition));
  return jsonb_build_object('project_id', pid, 'revision_id', rid);
end $$;
