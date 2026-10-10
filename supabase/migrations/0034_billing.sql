-- Phase 6.7: billing (Stripe, TEST mode only in this build). A firm's subscription status gates the creation of NEW projects; nothing already signed is
-- ever locked, hidden or deleted because a payment failed. Written only by the service (the webhook) and by the firm's own trial at creation.

create table subscription (
  firm_id uuid primary key references firm(id),
  status text not null default 'trialing' check (status in ('trialing', 'active', 'past_due', 'canceled')),
  plan_id text not null default 'trial' check (plan_id ~ '^[a-z][a-z0-9_-]{0,40}$'),
  seats int not null default 1 check (seats >= 0),
  max_projects int check (max_projects is null or max_projects >= 0),      -- null = unlimited
  trial_ends_at timestamptz not null default now() + interval '14 days',
  current_period_end timestamptz,
  stripe_customer_id text,
  stripe_subscription_id text,
  updated_at timestamptz not null default now()
);
create unique index subscription_stripe_sub on subscription (stripe_subscription_id) where stripe_subscription_id is not null;
alter table subscription enable row level security;
create policy subscription_select on subscription for select using (firm_id = current_firm_id());
revoke all on subscription from anon;
revoke insert, update, delete, truncate, trigger, references on subscription from authenticated;

insert into subscription (firm_id) select id from firm on conflict do nothing;
create function subscription_for_new_firm() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin insert into public.subscription (firm_id) values (new.id) on conflict do nothing; return new; end $$;
create trigger firm_subscription after insert on firm for each row execute function subscription_for_new_firm();

-- Stripe may send an event more than once: each id is processed once.
create table stripe_event (
  firm_id uuid references firm(id),      -- set when the event could be tied to a firm
  id text primary key check (id ~ '^evt_[A-Za-z0-9_]+$'),
  type text not null,
  received_at timestamptz not null default now()
);
alter table stripe_event enable row level security;
revoke all on stripe_event from anon, authenticated;

create function billing_blocks_projects(p_firm uuid) returns text language plpgsql stable security definer set search_path = public, pg_temp as
$$
declare s public.subscription; n int;
begin
  select * into s from public.subscription where firm_id = p_firm;
  if s.firm_id is null then return 'this firm has no subscription record'; end if;
  if s.status = 'canceled' then return 'the subscription is cancelled: renew it to start new projects (your existing projects and sign-offs are untouched)'; end if;
  if s.status = 'past_due' then return 'the last payment failed: update the payment method to start new projects (your existing projects and sign-offs are untouched)'; end if;
  if s.status = 'trialing' and s.trial_ends_at <= now() then return 'the trial has ended: subscribe to start new projects (your existing projects and sign-offs are untouched)'; end if;
  if s.max_projects is not null then
    select count(*) into n from public.project where firm_id = p_firm;
    if n >= s.max_projects then return 'your plan allows ' || s.max_projects || ' projects: upgrade to start another'; end if;
  end if;
  return null;
end $$;
revoke all on function billing_blocks_projects(uuid) from public, anon, authenticated;

create function project_create(p_address text, p_state text, p_edition text, p_climate_zone int, p_approval_date date) returns jsonb
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id(); why text; pid uuid := gen_random_uuid(); rid uuid := gen_random_uuid();
begin
  if auth.uid() is null or f is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer starts a project' using errcode = '42501';
  end if;
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
revoke all on function project_create(text, text, text, int, date) from public, anon;
grant execute on function project_create(text, text, text, int, date) to authenticated;

-- the active-seat count a subscription is billed for
create function billing_seats(p_firm uuid) returns int language sql stable security definer set search_path = public, pg_temp as
$$ select count(*)::int from public.app_user where firm_id = p_firm and active $$;
revoke all on function billing_seats(uuid) from public, anon, authenticated;
