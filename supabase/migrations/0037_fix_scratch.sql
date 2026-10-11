-- Phase 7.1: fix hypotheses. A scratch change records ONE proposed input change for a failed result and what the cross-rule re-run found. It decides nothing: the
-- change reaches the revision only when a designer applies it (as a hand-entered, UNCONFIRMED value that must go through Gate 1 again), and the API re-runs
-- the engine at that moment instead of trusting this row. Clients cannot write this table.
create table fix_scratch (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  subject_id text not null,
  rule_id text not null,
  option_id text not null,
  option jsonb not null,
  cross_rule jsonb not null,
  accepted boolean not null,
  status text not null default 'proposed' check (status in ('proposed', 'applied')),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  applied_at timestamptz,
  constraint fix_scratch_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint fix_scratch_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  applied_by uuid,
  check ((status = 'applied') = (applied_at is not null)),
  check ((status = 'applied') = (applied_by is not null))
);
create index fix_scratch_revision on fix_scratch (revision_id, created_at desc);
alter table fix_scratch enable row level security;
create policy fix_scratch_select on fix_scratch for select using (firm_id = current_firm_id());
revoke all on fix_scratch from anon;
revoke insert, update, delete, truncate, trigger, references on fix_scratch from authenticated;
create function fix_scratch_guard() returns trigger language plpgsql set search_path = public, pg_temp as
$$
begin
  if tg_op = 'DELETE' or (to_jsonb(new) - 'status' - 'applied_at' - 'applied_by') is distinct from (to_jsonb(old) - 'status' - 'applied_at' - 'applied_by') or old.status = 'applied' then
    raise exception 'a scratch change is a record: it can only be marked applied, once';
  end if;
  return new;
end $$;
create trigger fix_scratch_guard before update or delete on fix_scratch for each row execute function fix_scratch_guard();
create function fix_scratch_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (new.firm_id, new.revision_id, case when tg_op = 'INSERT' then 'fix_proposed' else 'fix_applied' end,
            jsonb_build_object('scratch', new.id, 'subject', new.subject_id, 'rule', new.rule_id, 'option', new.option, 'accepted', new.accepted, 'by', case when tg_op = 'INSERT' then new.created_by else coalesce(new.applied_by, new.created_by) end));
  return new;
end $$;
create trigger fix_scratch_frozen before insert on fix_scratch for each row execute function reject_if_revision_frozen();
create trigger fix_scratch_audit_ins after insert on fix_scratch for each row execute function fix_scratch_audit();
create trigger fix_scratch_audit_upd after update on fix_scratch for each row execute function fix_scratch_audit();
