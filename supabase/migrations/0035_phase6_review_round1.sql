-- Phase 6 review round 1.

-- (A1) The billing gate cannot be walked around: projects and revisions are created only by project_create / the service, never by a client INSERT.
revoke insert on project, revision from authenticated;
drop policy if exists project_firm_insert on project;
drop policy if exists revision_firm_insert on revision;

-- (A2) Nobody administers their own role or registration: another administrator does that.
create or replace function admin_set_role(p_user uuid, p_role user_role) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if p_user = auth.uid() then raise exception 'you cannot change your own role: ask another administrator'; end if;
  update public.app_user set role = p_role where id = p_user and firm_id = f;
  if not found then raise exception 'no such person in your firm'; end if;
end $$;

create or replace function registration_submit(p_user uuid, p_number text, p_register text, p_state_scheme text, p_evidence text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard(); rid uuid;
begin
  if p_user = auth.uid() then raise exception 'you cannot submit your own registration number: ask another administrator'; end if;
  if not exists (select 1 from public.app_user where id = p_user and firm_id = f and active and role = 'approver') then
    raise exception 'only an active approver of your firm carries a registration number';
  end if;
  update public.registration set status = 'superseded' where user_id = p_user and firm_id = f and status = 'submitted';
  insert into public.registration (firm_id, user_id, number, register, state_scheme, submitted_by, evidence)
    values (f, p_user, btrim(p_number), p_register, p_state_scheme, auth.uid(), btrim(p_evidence)) returning id into rid;
  insert into public.ledger_event (firm_id, kind, payload)
    values (f, 'registration_submitted', jsonb_build_object('registration', rid, 'user', p_user, 'number', btrim(p_number), 'register', p_register,
            'state_scheme', p_state_scheme, 'by', auth.uid()));
  return rid;
end $$;

-- the decider is never in the firm that submitted, and the permanent ledger keeps only a hash of the free-text note (the note itself is erasable)
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
  update public.registration set status = case when p_verify then 'verified' else 'rejected' end, decided_by = auth.uid(), decided_at = now(),
         decision_note = btrim(p_note) where id = p_id;
  if p_verify then
    update public.registration set status = 'superseded' where user_id = r.user_id and firm_id = r.firm_id and status = 'verified' and id <> p_id;
    select registration_no into old from public.app_user where id = r.user_id;
    perform set_config('mep.reg_path', 'verified', true);
    update public.app_user set registration_no = r.number where id = r.user_id and firm_id = r.firm_id;
  end if;
  insert into public.ledger_event (firm_id, kind, payload)
    values (r.firm_id, case when p_verify then 'registration_verified' else 'registration_rejected' end,
            jsonb_build_object('registration', p_id, 'user', r.user_id, 'number', r.number, 'register', r.register, 'state_scheme', r.state_scheme,
                               'previous', old, 'decided_by', auth.uid(), 'note_sha256', encode(sha256(convert_to(btrim(p_note), 'utf8')), 'hex')));
end $$;

-- (A3) two administrators cannot remove each other: the firm row is locked while the count is taken
create or replace function admin_set_admin(p_user uuid, p_admin boolean) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  perform 1 from public.firm where id = f for update;
  if not p_admin and (select count(*) from public.app_user where firm_id = f and is_admin and active and id <> p_user) = 0 then
    raise exception 'the firm needs at least one active administrator';
  end if;
  update public.app_user set is_admin = p_admin where id = p_user and firm_id = f and active;
  if not found then raise exception 'no such active person in your firm'; end if;
end $$;

create or replace function admin_set_active(p_user uuid, p_active boolean) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  perform 1 from public.firm where id = f for update;
  if not p_active and p_user = auth.uid() then raise exception 'you cannot deactivate yourself'; end if;
  if not p_active and (select is_admin from public.app_user where id = p_user and firm_id = f)
     and (select count(*) from public.app_user where firm_id = f and is_admin and active and id <> p_user) = 0 then
    raise exception 'the firm needs at least one active administrator';
  end if;
  update public.app_user set active = p_active where id = p_user and firm_id = f;
  if not found then raise exception 'no such person in your firm'; end if;
end $$;

-- (A4) an invitation does not reveal whether an address belongs to another firm
create or replace function admin_invite(p_email text, p_role user_role) returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard(); e text := lower(btrim(p_email)); iid uuid;
begin
  if exists (select 1 from public.app_user where email = e) then
    raise exception 'that address cannot be invited (it already belongs to a member of a firm)';
  end if;
  update public.invitation set status = 'revoked' where firm_id = f and email = e and status = 'pending' and expires_at < now();
  insert into public.invitation (firm_id, email, role, invited_by) values (f, e, p_role, auth.uid()) returning id into iid;
  insert into public.ledger_event (firm_id, kind, payload)
    values (f, 'user_invited', jsonb_build_object('invitation', iid, 'role', p_role, 'by', auth.uid()));
  return iid;
end $$;

-- (A5) the person chooses which invitation to accept (several firms may have invited the same address)
drop function accept_invitation();
create function accept_invitation(p_invitation uuid default null) returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare e text; inv public.invitation; n int;
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if exists (select 1 from public.app_user where id = auth.uid()) then raise exception 'you already belong to a firm'; end if;
  select lower(email) into e from auth.users where id = auth.uid() and email_confirmed_at is not null;
  if e is null then raise exception 'your address is not confirmed' using errcode = '42501'; end if;
  select count(*) into n from public.invitation where email = e and status = 'pending' and expires_at > now();
  if n = 0 then raise exception 'there is no open invitation for this address'; end if;
  if p_invitation is null and n > 1 then raise exception 'several firms have invited you: choose one'; end if;
  select * into inv from public.invitation where email = e and status = 'pending' and expires_at > now() and (p_invitation is null or id = p_invitation)
    order by created_at desc limit 1;
  if inv.id is null then raise exception 'that invitation is not open for your address'; end if;
  insert into public.app_user (id, firm_id, role, email) values (auth.uid(), inv.firm_id, inv.role, e);
  update public.invitation set status = 'accepted', accepted_by = auth.uid(), accepted_at = now() where id = inv.id;
  insert into public.ledger_event (firm_id, kind, payload)
    values (inv.firm_id, 'invitation_accepted', jsonb_build_object('invitation', inv.id, 'user', auth.uid(), 'role', inv.role));
  return inv.firm_id;
end $$;
revoke all on function accept_invitation(uuid) from public, anon;
grant execute on function accept_invitation(uuid) to authenticated;

-- (A6) Stripe: remember the newest event applied, so a late event cannot undo a newer one
alter table subscription add column stripe_last_event_at bigint not null default 0;
