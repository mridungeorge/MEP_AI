-- Phase 3: revision lineage, persisted run results, diff confirmation, freezing.

-- 1. Run results are stored per (revision, system, rule). A new run supersedes the old one (current = false), never edits it:
--    the result columns stay immutable (0003).
alter table rule_result
  add column subject_id text,
  add column part int,
  add column run_id uuid,
  add column causes jsonb not null default '[]',
  add column near_miss jsonb,
  add column current boolean not null default true;
create unique index rule_result_current_uq on rule_result (revision_id, subject_id, rule_id) where current and subject_id is not null;
create index rule_result_run_idx on rule_result (revision_id, run_id);

-- 2. A space's position in the plan (metres), so spaces without an IFC GUID can be matched between revisions by name + centroid.
alter table space add column centroid_x_m numeric, add column centroid_y_m numeric;

-- 3. Lineage: a new architect revision is a CHILD of a FROZEN parent of the same firm (and project: 0003). Children are made by
--    the service on upload, never by a client.
create function revision_parent_frozen() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if new.parent_revision_id is not null and not exists (
       select 1 from public.revision p
        where p.id = new.parent_revision_id and p.firm_id = new.firm_id and p.frozen_at is not null) then
    raise exception 'a revision can only be made from a frozen parent of the same firm';
  end if;
  return new;
end $$;
create trigger revision_parent_frozen_check before insert on revision
  for each row execute function revision_parent_frozen();

-- 4. The designer confirms the DIFF between a child and its parent (Gate 1 again, changed values only). The confirmation names the
--    hash of the diff as it stood; the API refuses a run when the live diff no longer hashes to it.
create table revision_diff (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  parent_revision_id uuid not null,
  diff_hash text not null check (diff_hash ~ '^[0-9a-f]{64}$'),
  confirmed_by uuid not null,
  confirmed_at timestamptz not null default now(),
  constraint revision_diff_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint revision_diff_confirmed_by_fk foreign key (confirmed_by, firm_id) references app_user (id, firm_id),
  unique (revision_id)
);
alter table revision_diff enable row level security;
create policy revision_diff_firm_select on revision_diff for select using (firm_id = current_firm_id());
revoke all on revision_diff from anon;
revoke insert, update, delete, truncate, trigger, references on revision_diff from authenticated;

create function confirm_revision_diff(p_revision uuid, p_hash text) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare parent uuid; frozen timestamptz;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer confirms a revision diff' using errcode = '42501';
  end if;
  select parent_revision_id, frozen_at into parent, frozen from public.revision
    where id = p_revision and firm_id = public.current_firm_id();
  if not found then
    raise exception 'revision not found in your firm' using errcode = '42501';
  end if;
  if parent is null then raise exception 'this revision has no parent: there is no diff to confirm'; end if;
  if frozen is not null then raise exception 'revision is frozen'; end if;
  insert into public.revision_diff (firm_id, revision_id, parent_revision_id, diff_hash, confirmed_by)
    values (public.current_firm_id(), p_revision, parent, p_hash, auth.uid())
    on conflict (revision_id) do update set diff_hash = excluded.diff_hash, confirmed_by = excluded.confirmed_by,
                                            confirmed_at = now();
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'diff_confirm',
            jsonb_build_object('parent', parent, 'diff_hash', p_hash, 'confirmed_by', auth.uid(), 'role', 'designer'));
end $$;
revoke all on function confirm_revision_diff(uuid, text) from public, anon;
grant execute on function confirm_revision_diff(uuid, text) to authenticated;

-- 5. Freezing: a designer freezes a revision that has current results (and, for a child, a confirmed diff is checked by the API).
--    Phase 4 puts this behind the gate state machine; for now it is what makes a revision a lineage parent.
create function freeze_revision(p_revision uuid) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer freezes a revision' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = public.current_firm_id()) then
    raise exception 'revision not found in your firm' using errcode = '42501';
  end if;
  if not exists (select 1 from public.rule_result where revision_id = p_revision and firm_id = public.current_firm_id()
                   and current) then
    raise exception 'run the rules before freezing: there are no current results';
  end if;
  update public.revision set frozen_at = now(), status = 'frozen'
    where id = p_revision and firm_id = public.current_firm_id() and frozen_at is null;
  if not found then raise exception 'revision is already frozen'; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'revision_frozen',
            jsonb_build_object('frozen_by', auth.uid(), 'role', 'designer'));
end $$;
revoke all on function freeze_revision(uuid) from public, anon;
grant execute on function freeze_revision(uuid) to authenticated;
