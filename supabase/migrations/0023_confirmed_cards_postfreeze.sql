-- Phase 5: (2) an agent builds only from a spec card version a designer confirmed; (5) agent notes made after a freeze are post-freeze annotations.

create table spec_confirmation (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  skill text not null check (skill ~ '^[a-z][a-z0-9-]{1,40}$'),
  spec_sha256 text not null check (spec_sha256 ~ '^[0-9a-f]{64}$'),
  note_id uuid references agent_note (id),
  confirmed_by uuid not null,
  confirmed_at timestamptz not null default now(),
  constraint spec_confirmation_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint spec_confirmation_user_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  unique (revision_id, skill, spec_sha256)
);
alter table spec_confirmation enable row level security;
create policy spec_confirmation_firm_select on spec_confirmation for select using (firm_id = current_firm_id());
revoke all on spec_confirmation from anon;
revoke insert, update, delete, truncate, trigger, references on spec_confirmation from authenticated;
create trigger spec_confirmation_append_only before update or delete on spec_confirmation for each row execute function review_append_only();
create trigger spec_confirmation_audit after insert on spec_confirmation for each row execute function audit_row();

-- A human designer confirms the EXACT effective card (defaults and constants applied); the hash is computed by the API from the card the
-- designer was shown. Only a person with a session can call this; an agent has no such door.
create function spec_card_confirm(p_revision uuid, p_skill text, p_sha256 text, p_note uuid default null) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare sid uuid;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer confirms a spec card' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = public.current_firm_id() and frozen_at is null) then
    raise exception 'no such open revision in your firm' using errcode = '42501';
  end if;
  insert into public.spec_confirmation (firm_id, revision_id, skill, spec_sha256, note_id, confirmed_by)
    values (public.current_firm_id(), p_revision, p_skill, p_sha256, p_note, auth.uid())
    on conflict (revision_id, skill, spec_sha256) do update set spec_sha256 = excluded.spec_sha256
    returning id into sid;
  return sid;
end $$;
revoke all on function spec_card_confirm(uuid, text, text, uuid) from public, anon;
grant execute on function spec_card_confirm(uuid, text, text, uuid) to authenticated;

-- (5) notes written after the revision was frozen say so, and the database decides that (not the caller).
alter table agent_note add column post_freeze boolean not null default false;
create function agent_note_mark_post_freeze() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
  new.post_freeze := exists (select 1 from revision r where r.id = new.revision_id and r.firm_id = new.firm_id and r.frozen_at is not null);
  return new;
end $$;
create trigger agent_note_post_freeze before insert on agent_note for each row execute function agent_note_mark_post_freeze();
