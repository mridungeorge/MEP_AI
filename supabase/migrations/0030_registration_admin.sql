-- Phase 6.2: approver registration. A firm administrator SUBMITS a registration number and the register it is on; a platform administrator (the
-- operator, a different person from the firm) VERIFIES it against the public register and records what they checked. Only a verified
-- registration reaches app_user.registration_no through the app; the service-only script remains as a logged break-glass path.

create table platform_admin (user_id uuid primary key references auth.users (id), created_at timestamptz not null default now());
alter table platform_admin enable row level security;
revoke all on platform_admin from anon, authenticated;

create function is_platform_admin() returns boolean language sql stable security definer set search_path = public, pg_temp as
$$ select exists (select 1 from public.platform_admin p join public.app_user u on u.id = p.user_id where p.user_id = auth.uid() and u.active) $$;
revoke all on function is_platform_admin() from public, anon;
grant execute on function is_platform_admin() to authenticated;

create table registration (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  user_id uuid not null,
  number text not null check (number ~ '^[A-Za-z0-9][A-Za-z0-9 ./-]{2,39}$'),
  register text not null check (register in ('NER', 'RPEQ', 'STATE')),
  state_scheme text check (state_scheme is null or length(state_scheme) between 2 and 80),
  status text not null default 'submitted' check (status in ('submitted', 'verified', 'rejected', 'superseded')),
  submitted_by uuid not null,
  submitted_at timestamptz not null default now(),
  evidence text not null check (length(evidence) between 15 and 1000),
  decided_by uuid,
  decided_at timestamptz,
  decision_note text check (decision_note is null or length(decision_note) <= 1000),
  constraint registration_user_fk foreign key (user_id, firm_id) references app_user (id, firm_id),
  constraint registration_submitter_fk foreign key (submitted_by, firm_id) references app_user (id, firm_id),
  check (register <> 'STATE' or state_scheme is not null),
  check (status not in ('verified', 'rejected') or decided_by is not null)
);
create index registration_pending on registration (status, submitted_at);
alter table registration enable row level security;
create policy registration_select on registration for select
  using (firm_id = current_firm_id() and (current_user_is_admin() or user_id = auth.uid()));
revoke all on registration from anon;
revoke insert, update, delete, truncate, trigger, references on registration from authenticated;

-- every registration change is visible in the audit trail of the person (who, what, by which path)
create or replace function app_user_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if tg_op = 'INSERT' or (new.registration_no, new.role, new.also_roles, new.is_admin, new.active)
                         is distinct from (old.registration_no, old.role, old.also_roles, old.is_admin, old.active) then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.firm_id, 'app_user_changed', jsonb_build_object(
        'user_id', new.id, 'role', new.role, 'also_roles', new.also_roles, 'registration_no', new.registration_no,
        'is_admin', new.is_admin, 'active', new.active,
        'registration_path', case when tg_op = 'UPDATE' and new.registration_no is distinct from old.registration_no
                                  then coalesce(nullif(current_setting('mep.reg_path', true), ''), 'direct') end,
        'previous', case when tg_op = 'UPDATE' then jsonb_build_object('role', old.role, 'also_roles', old.also_roles,
                                                                         'registration_no', old.registration_no,
                                                                         'is_admin', old.is_admin, 'active', old.active) end));
  end if;
  return new;
end $$;

create function registration_submit(p_user uuid, p_number text, p_register text, p_state_scheme text, p_evidence text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard(); rid uuid;
begin
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

-- the platform administrator's queue: across firms, newest last
create function platform_pending_registrations() returns table (id uuid, firm text, user_email text, number text, register text, state_scheme text,
                                                             evidence text, submitted_at timestamptz, submitted_by_email text)
language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  if not public.is_platform_admin() then raise exception 'platform administrators only' using errcode = '42501'; end if;
  return query select r.id, f.name, u.email, r.number, r.register, r.state_scheme, r.evidence, r.submitted_at, s.email
    from public.registration r join public.firm f on f.id = r.firm_id join public.app_user u on u.id = r.user_id join public.app_user s on s.id = r.submitted_by
    where r.status = 'submitted' order by r.submitted_at;
end $$;

create function registration_decide(p_id uuid, p_verify boolean, p_note text) returns void language plpgsql security definer
set search_path = public, pg_temp as
$$
declare r public.registration; old text;
begin
  if not public.is_platform_admin() then raise exception 'platform administrators only' using errcode = '42501'; end if;
  select * into r from public.registration where id = p_id and status = 'submitted' for update;
  if r.id is null then raise exception 'no such registration awaiting a decision'; end if;
  if r.user_id = auth.uid() or r.submitted_by = auth.uid() then raise exception 'you cannot decide a registration you submitted or that is yours'; end if;
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
                               'previous', old, 'decided_by', auth.uid(), 'note', btrim(p_note)));
end $$;

revoke all on function registration_submit(uuid, text, text, text, text), platform_pending_registrations(), registration_decide(uuid, boolean, text)
  from public, anon;
grant execute on function registration_submit(uuid, text, text, text, text), platform_pending_registrations(), registration_decide(uuid, boolean, text)
  to authenticated;
