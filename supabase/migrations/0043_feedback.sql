-- Phase 10.8: in-app feedback. A signed-in member sends a short message about a page; administrators of the firm read them. Append-only; no attachments.
create table feedback (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  created_by uuid not null,
  kind text not null check (kind in ('bug', 'idea', 'question', 'other')),
  page text check (page is null or length(page) <= 200),
  message text not null check (length(btrim(message)) between 3 and 2000),
  created_at timestamptz not null default now(),
  constraint feedback_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id)
);
create index feedback_firm on feedback (firm_id, created_at desc);
alter table feedback enable row level security;
create policy feedback_select on feedback for select using (firm_id = current_firm_id() and (created_by = auth.uid() or current_user_is_admin()));
revoke all on feedback from anon;
revoke insert, update, delete, truncate, trigger, references on feedback from authenticated;
create trigger feedback_append_only before update or delete on feedback for each row execute function review_append_only();

create function submit_feedback(p_kind text, p_page text, p_message text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id(); fid uuid;
begin
  if auth.uid() is null or f is null then raise exception 'sign in to send feedback' using errcode = '42501'; end if;
  if (select count(*) from public.feedback where created_by = auth.uid() and created_at > now() - interval '1 hour') >= 20 then
    raise exception 'too much feedback in the last hour; try again later';
  end if;
  insert into public.feedback (firm_id, created_by, kind, page, message) values (f, auth.uid(), p_kind, left(p_page, 200), p_message) returning id into fid;
  return fid;
end $$;
revoke all on function submit_feedback(text, text, text) from public, anon;
grant execute on function submit_feedback(text, text, text) to authenticated;
