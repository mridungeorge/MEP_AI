-- Phase 6.4: e-mail notifications. The database decides WHO is told WHAT (triggers write an outbox row, honouring each person's preferences); a sender
-- (Resend) delivers pending rows. A notification never carries results or values, only that something needs attention and a link into the app.

alter table app_user add column notify_prefs jsonb not null default '{}';

create table notification (
  id bigint generated always as identity primary key,
  firm_id uuid not null references firm(id),
  user_id uuid not null,
  kind text not null check (kind in ('review_requested', 'changes_requested', 'signed', 'share_link_opened')),
  revision_id uuid,
  subject text not null check (length(subject) <= 200),
  body text not null check (length(body) <= 1000),
  link_path text check (link_path is null or link_path like '/%'),
  status text not null default 'pending' check (status in ('pending', 'sent', 'skipped', 'failed')),
  attempts int not null default 0,
  error text,
  created_at timestamptz not null default now(),
  sent_at timestamptz,
  constraint notification_user_fk foreign key (user_id, firm_id) references app_user (id, firm_id)
);
create index notification_pending on notification (status, created_at) where status = 'pending';
alter table notification enable row level security;
create policy notification_own_select on notification for select using (firm_id = current_firm_id() and user_id = auth.uid());
revoke all on notification from anon;
revoke insert, update, delete, truncate, trigger, references on notification from authenticated;

-- queue one notification for one person, unless they switched that kind off or are not active / have no address
create function notify_user(p_user uuid, p_kind text, p_revision uuid, p_subject text, p_body text, p_link text) returns void
language plpgsql security definer set search_path = public, pg_temp as
$$
declare u public.app_user;
begin
  select * into u from public.app_user where id = p_user;
  if u.id is null or not u.active or u.email is null then return; end if;
  if coalesce((u.notify_prefs ->> p_kind)::boolean, true) is not true then return; end if;
  insert into public.notification (firm_id, user_id, kind, revision_id, subject, body, link_path)
    values (u.firm_id, u.id, p_kind, p_revision, left(p_subject, 200), left(p_body, 1000), p_link);
end $$;
revoke all on function notify_user(uuid, text, uuid, text, text, text) from public, anon, authenticated;

create function rev_label(p_revision uuid) returns text language sql stable security definer set search_path = public, pg_temp as
$$ select p.address || ', Rev ' || r.architect_rev from public.revision r join public.project p on p.id = r.project_id where r.id = p_revision $$;
revoke all on function rev_label(uuid) from public, anon, authenticated;

create function rev_link(p_revision uuid, p_page text) returns text language sql stable security definer set search_path = public, pg_temp as
$$ select '/projects/' || r.project_id || '/revisions/' || r.id || '/' || p_page from public.revision r where r.id = p_revision $$;
revoke all on function rev_link(uuid, text) from public, anon, authenticated;

create function notify_on_signoff() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$
declare lbl text := public.rev_label(new.revision_id); who record; gate1_user uuid;
begin
  -- the next person in the chain is asked to act
  if new.gate = 'gate1' then
    for who in select id from public.app_user where firm_id = new.firm_id and active and (role = 'checker' or 'checker' = any(also_roles)) and id <> new.user_id loop
      perform public.notify_user(who.id, 'review_requested', new.revision_id, 'Review requested: ' || lbl,
        'A revision is frozen and ready for your Gate 2 review.', public.rev_link(new.revision_id, 'review'));
    end loop;
  elsif new.gate = 'gate2' then
    for who in select id from public.app_user where firm_id = new.firm_id and active and (role = 'approver' or 'approver' = any(also_roles)) and id <> new.user_id loop
      perform public.notify_user(who.id, 'review_requested', new.revision_id, 'Ready for Gate 3: ' || lbl,
        'Gate 2 has been signed. The revision is ready for your Gate 3 sign-off.', public.rev_link(new.revision_id, 'review'));
    end loop;
  end if;
  -- and the people who started it hear that it was signed
  select user_id into gate1_user from public.signoff where revision_id = new.revision_id and gate = 'gate1' limit 1;
  for who in select id from public.app_user where firm_id = new.firm_id and active and id <> new.user_id and (id = gate1_user or is_admin) loop
    perform public.notify_user(who.id, 'signed', new.revision_id, upper(new.gate::text) || ' signed: ' || lbl,
      upper(new.gate::text) || ' was signed for this revision.', public.rev_link(new.revision_id, 'review'));
  end loop;
  return new;
end $$;
create trigger signoff_notify after insert on signoff for each row execute function notify_on_signoff();

create function notify_on_review() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$
declare rev uuid; g1 uuid;
begin
  if new.decision <> 'request_changes' then return new; end if;
  select revision_id into rev from public.rule_result where id = new.rule_result_id;
  if rev is null then return new; end if;
  select user_id into g1 from public.signoff where revision_id = rev and gate = 'gate1' limit 1;
  if g1 is not null and g1 <> new.user_id then
    perform public.notify_user(g1, 'changes_requested', rev, 'Changes requested: ' || public.rev_label(rev),
      'The checker has asked for a change on one of the results.', public.rev_link(rev, 'review'));
  end if;
  return new;
end $$;
create trigger review_notify after insert on review for each row execute function notify_on_review();

create function notify_on_share_open() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  -- the person who made the link hears when it is opened (at most once an hour per link)
  if new.views > old.views and (old.last_viewed_at is null or old.last_viewed_at < now() - interval '1 hour') and new.created_by is not null then
    perform public.notify_user(new.created_by, 'share_link_opened', new.revision_id, 'Your share link was opened: ' || public.rev_label(new.revision_id),
      'Someone opened the certifier link you created.', public.rev_link(new.revision_id, 'review'));
  end if;
  return new;
end $$;
create trigger ledger_link_notify after update on ledger_link for each row execute function notify_on_share_open();

-- a person reads and changes their own preferences
create function my_notify_prefs() returns jsonb language sql stable security definer set search_path = public, pg_temp as
$$ select coalesce(notify_prefs, '{}') from public.app_user where id = auth.uid() and active $$;
create function set_my_notify_prefs(p_prefs jsonb) returns void language plpgsql security definer set search_path = public, pg_temp as
$$
declare k text; clean jsonb := '{}';
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  for k in select jsonb_object_keys(p_prefs) loop
    if k not in ('review_requested', 'changes_requested', 'signed', 'share_link_opened') then raise exception 'unknown notification kind %', k; end if;
    if jsonb_typeof(p_prefs -> k) <> 'boolean' then raise exception '% is on or off', k; end if;
    clean := clean || jsonb_build_object(k, p_prefs -> k);
  end loop;
  update public.app_user set notify_prefs = clean where id = auth.uid() and active;
end $$;
revoke all on function my_notify_prefs(), set_my_notify_prefs(jsonb) from public, anon;
grant execute on function my_notify_prefs(), set_my_notify_prefs(jsonb) to authenticated;
