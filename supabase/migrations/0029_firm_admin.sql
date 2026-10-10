-- Phase 6.1: firm administration. A firm admin invites people by e-mail, assigns roles, deactivates users and sets the firm's settings. Every
-- change is a ledger entry (the app_user / firm audit triggers, extended here, plus explicit events). No client can write these tables directly:
-- everything goes through the security-definer functions below, which check that the caller is an active admin of the same firm.

alter table app_user
  add column is_admin boolean not null default false,
  add column active boolean not null default true,
  add column email text;
update app_user u set email = lower(a.email) from auth.users a where a.id = u.id and u.email is null;
-- existing firms have no admin yet: their approver (else their designer) administers until others are named
update app_user u set is_admin = true
  where not exists (select 1 from app_user x where x.firm_id = u.firm_id and x.is_admin)
    and u.id = (select y.id from app_user y where y.firm_id = u.firm_id order by (y.role = 'approver') desc, (y.role = 'designer') desc, y.id limit 1);

create function app_user_fill_email() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if new.email is null then new.email := (select lower(email) from auth.users where id = new.id); end if;
  return new;
end $$;
create trigger app_user_fill_email before insert on app_user for each row execute function app_user_fill_email();

alter table firm
  add column near_miss_default numeric check (near_miss_default is null or (near_miss_default > 0 and near_miss_default <= 0.5)),
  add constraint firm_sample_size_sane check (sample_size <= 1000);

-- a deactivated person is nobody: no firm, no role, so row-level security shows them nothing
create or replace function current_firm_id() returns uuid language sql stable security definer
  set search_path = public, pg_temp as
$$ select firm_id from public.app_user where id = auth.uid() and active $$;

create or replace function current_user_role() returns user_role language sql stable security definer
  set search_path = public, pg_temp as
$$
  select case
           when f.signer_mode = 'small_firm'
                and (coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'acting_role') is not null
                and (coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'acting_role')::text
                    = any (array(select r::text from unnest(u.also_roles || u.role) r))
           then (coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb ->> 'acting_role')::user_role
           else u.role
         end
    from public.app_user u join public.firm f on f.id = u.firm_id where u.id = auth.uid() and u.active
$$;

create function current_user_is_admin() returns boolean language sql stable security definer set search_path = public, pg_temp as
$$ select coalesce((select is_admin and active from public.app_user where id = auth.uid()), false) $$;
revoke all on function current_user_is_admin() from public, anon;
grant execute on function current_user_is_admin() to authenticated;

-- the audit trigger now also records who is an admin and who is active
create or replace function app_user_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if tg_op = 'INSERT' or (new.registration_no, new.role, new.also_roles, new.is_admin, new.active)
                         is distinct from (old.registration_no, old.role, old.also_roles, old.is_admin, old.active) then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.firm_id, 'app_user_changed', jsonb_build_object(
        'user_id', new.id, 'role', new.role, 'also_roles', new.also_roles, 'registration_no', new.registration_no,
        'is_admin', new.is_admin, 'active', new.active,
        'previous', case when tg_op = 'UPDATE' then jsonb_build_object('role', old.role, 'also_roles', old.also_roles,
                                                                         'registration_no', old.registration_no,
                                                                         'is_admin', old.is_admin, 'active', old.active) end));
  end if;
  return new;
end $$;

create or replace function firm_setting_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if new.signer_mode is distinct from old.signer_mode then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_signer_mode_changed', jsonb_build_object('from', old.signer_mode, 'to', new.signer_mode, 'by', auth.uid()));
  end if;
  if (new.sample_size, new.near_miss_default, new.name) is distinct from (old.sample_size, old.near_miss_default, old.name) then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_settings_changed', jsonb_build_object('by', auth.uid(),
        'sample_size', jsonb_build_array(old.sample_size, new.sample_size),
        'near_miss_default', jsonb_build_array(old.near_miss_default, new.near_miss_default),
        'name', jsonb_build_array(old.name, new.name)));
  end if;
  return new;
end $$;

-- ---- invitations -------------------------------------------------------------------------------------------------------------
create table invitation (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  email text not null check (email = lower(email) and email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' and length(email) <= 254),
  role user_role not null,
  invited_by uuid not null,
  status text not null default 'pending' check (status in ('pending', 'accepted', 'revoked')),
  expires_at timestamptz not null default now() + interval '14 days',
  created_at timestamptz not null default now(),
  accepted_by uuid,
  accepted_at timestamptz,
  constraint invitation_inviter_fk foreign key (invited_by, firm_id) references app_user (id, firm_id)
);
create unique index invitation_one_pending on invitation (firm_id, email) where status = 'pending';
alter table invitation enable row level security;
create policy invitation_admin_select on invitation for select using (firm_id = current_firm_id() and current_user_is_admin());
revoke all on invitation from anon;
revoke insert, update, delete, truncate, trigger, references on invitation from authenticated;

create function admin_guard() returns uuid language plpgsql stable security definer set search_path = public, pg_temp as
$$
begin
  if auth.uid() is null or not public.current_user_is_admin() or public.current_firm_id() is null then
    raise exception 'only an administrator of the firm may do this' using errcode = '42501';
  end if;
  return public.current_firm_id();
end $$;
revoke all on function admin_guard() from public, anon;

create function admin_invite(p_email text, p_role user_role) returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard(); e text := lower(btrim(p_email)); iid uuid;
begin
  if exists (select 1 from public.app_user where firm_id = f and email = e) then
    raise exception 'that person is already a member of the firm';
  end if;
  if exists (select 1 from public.app_user where email = e and firm_id <> f) then
    raise exception 'that address already belongs to another firm';
  end if;
  update public.invitation set status = 'revoked' where firm_id = f and email = e and status = 'pending' and expires_at < now();
  insert into public.invitation (firm_id, email, role, invited_by) values (f, e, p_role, auth.uid()) returning id into iid;
  -- the ledger is permanent: it records the invitation, not the e-mail address
  insert into public.ledger_event (firm_id, kind, payload)
    values (f, 'user_invited', jsonb_build_object('invitation', iid, 'role', p_role, 'by', auth.uid()));
  return iid;
end $$;

create function admin_revoke_invitation(p_id uuid) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  update public.invitation set status = 'revoked' where id = p_id and firm_id = f and status = 'pending';
  if not found then raise exception 'no such pending invitation'; end if;
  insert into public.ledger_event (firm_id, kind, payload) values (f, 'invitation_revoked', jsonb_build_object('invitation', p_id, 'by', auth.uid()));
end $$;

-- A person who signed in (magic link) but belongs to no firm yet claims the invitation addressed to their verified e-mail.
create function accept_invitation() returns uuid language plpgsql security definer set search_path = public, pg_temp as
$$
declare e text; inv public.invitation;
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if exists (select 1 from public.app_user where id = auth.uid()) then raise exception 'you already belong to a firm'; end if;
  select lower(email) into e from auth.users where id = auth.uid() and email_confirmed_at is not null;
  if e is null then raise exception 'your address is not confirmed' using errcode = '42501'; end if;
  select * into inv from public.invitation where email = e and status = 'pending' and expires_at > now() order by created_at desc limit 1;
  if inv.id is null then raise exception 'there is no open invitation for this address'; end if;
  insert into public.app_user (id, firm_id, role, email) values (auth.uid(), inv.firm_id, inv.role, e);
  update public.invitation set status = 'accepted', accepted_by = auth.uid(), accepted_at = now() where id = inv.id;
  insert into public.ledger_event (firm_id, kind, payload)
    values (inv.firm_id, 'invitation_accepted', jsonb_build_object('invitation', inv.id, 'user', auth.uid(), 'role', inv.role));
  return inv.firm_id;
end $$;

create function admin_set_role(p_user uuid, p_role user_role) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  update public.app_user set role = p_role where id = p_user and firm_id = f;
  if not found then raise exception 'no such person in your firm'; end if;
end $$;

create function admin_set_admin(p_user uuid, p_admin boolean) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if not p_admin and (select count(*) from public.app_user where firm_id = f and is_admin and active and id <> p_user) = 0 then
    raise exception 'the firm needs at least one active administrator';
  end if;
  update public.app_user set is_admin = p_admin where id = p_user and firm_id = f and active;
  if not found then raise exception 'no such active person in your firm'; end if;
end $$;

create function admin_set_active(p_user uuid, p_active boolean) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if not p_active and p_user = auth.uid() then raise exception 'you cannot deactivate yourself'; end if;
  if not p_active and (select is_admin from public.app_user where id = p_user and firm_id = f)
     and (select count(*) from public.app_user where firm_id = f and is_admin and active and id <> p_user) = 0 then
    raise exception 'the firm needs at least one active administrator';
  end if;
  update public.app_user set active = p_active where id = p_user and firm_id = f;
  if not found then raise exception 'no such person in your firm'; end if;
end $$;

create function admin_set_settings(p_name text, p_signer_mode text, p_sample_size int, p_near_miss numeric) returns void
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard();
begin
  if p_signer_mode not in ('strict', 'small_firm') then raise exception 'signer mode is strict or small_firm'; end if;
  if p_sample_size is null or p_sample_size < 1 or p_sample_size > 1000 then raise exception 'the spot-check sample size is 1 to 1000'; end if;
  update public.firm set name = coalesce(nullif(btrim(p_name), ''), name), signer_mode = p_signer_mode, sample_size = p_sample_size,
         near_miss_default = p_near_miss where id = f;
end $$;

-- ---- firm templates: title block and layer standard ------------------------------------------------------------------------------
create table firm_template (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  kind text not null check (kind in ('title_block', 'layer_standard')),
  name text not null check (length(name) between 1 and 120),
  media_type text not null,
  content bytea not null check (length(content) between 1 and 2097152),
  sha256 text not null check (sha256 ~ '^[0-9a-f]{64}$'),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  constraint firm_template_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id)
);
create index firm_template_latest on firm_template (firm_id, kind, created_at desc);
alter table firm_template enable row level security;
create policy firm_template_select on firm_template for select using (firm_id = current_firm_id());
revoke all on firm_template from anon;
revoke insert, update, delete, truncate, trigger, references on firm_template from authenticated;
revoke select on firm_template from authenticated;
grant select (id, firm_id, kind, name, media_type, sha256, created_by, created_at) on firm_template to authenticated;
create function firm_template_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  insert into public.ledger_event (firm_id, kind, payload)
    values (new.firm_id, 'firm_template_added', jsonb_build_object('id', new.id, 'kind', new.kind, 'name', new.name, 'sha256', new.sha256, 'by', new.created_by));
  return new;
end $$;
create trigger firm_template_audit after insert on firm_template for each row execute function firm_template_audit();

create function admin_template_content(p_id uuid) returns table (name text, media_type text, content bytea)
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id();
begin
  if f is null or auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  return query select t.name, t.media_type, t.content from public.firm_template t where t.id = p_id and t.firm_id = f;
end $$;
revoke all on function admin_template_content(uuid) from public, anon;
grant execute on function admin_template_content(uuid) to authenticated;

create function admin_add_template(p_kind text, p_name text, p_media_type text, p_content bytea, p_sha256 text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.admin_guard(); tid uuid;
begin
  insert into public.firm_template (firm_id, kind, name, media_type, content, sha256, created_by)
    values (f, p_kind, p_name, p_media_type, p_content, p_sha256, auth.uid()) returning id into tid;
  return tid;
end $$;

revoke all on function admin_invite(text, user_role), admin_revoke_invitation(uuid), accept_invitation(), admin_set_role(uuid, user_role),
  admin_set_admin(uuid, boolean), admin_set_active(uuid, boolean), admin_set_settings(text, text, int, numeric),
  admin_add_template(text, text, text, bytea, text) from public, anon;
grant execute on function admin_invite(text, user_role), admin_revoke_invitation(uuid), accept_invitation(), admin_set_role(uuid, user_role),
  admin_set_admin(uuid, boolean), admin_set_active(uuid, boolean), admin_set_settings(text, text, int, numeric),
  admin_add_template(text, text, text, bytea, text) to authenticated;
