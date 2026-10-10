-- Phase 9.2: site measurements re-imported from a commissioning sheet. A reading is a RECORD of what a technician measured: never a rule result, never an input.
-- Batches and readings are append-only; one ledger event is written per batch.
create table commissioning_batch (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  file_sha256 text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
  tolerance_pct numeric not null check (tolerance_pct >= 0 and tolerance_pct <= 50),
  counts jsonb not null,
  imported_by uuid not null,
  imported_at timestamptz not null default now(),
  constraint commissioning_batch_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint commissioning_batch_user_fk foreign key (imported_by, firm_id) references app_user (id, firm_id),
  unique (id, firm_id)
);
create table commissioning_reading (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  batch_id uuid not null,
  revision_id uuid not null,
  terminal text not null check (length(terminal) <= 80),
  system_tag text not null check (length(system_tag) <= 80),
  design_ls numeric,
  measured_ls numeric check (measured_ls is null or measured_ls >= 0),
  variance_pct numeric,
  tolerance_pct numeric not null,
  flag text not null check (flag in ('WITHIN_TOLERANCE', 'OUTSIDE_TOLERANCE', 'NOT_MEASURED', 'INVALID', 'DESIGN_CHANGED', 'NOT_IN_DESIGN', 'DUPLICATE', 'MISSING_FROM_SHEET')),
  measured_on text check (length(measured_on) <= 40),
  measured_by text check (length(measured_by) <= 80),
  comment text check (length(comment) <= 300),
  constraint commissioning_reading_batch_fk foreign key (batch_id, firm_id) references commissioning_batch (id, firm_id)
);
create index commissioning_reading_batch on commissioning_reading (batch_id);
alter table commissioning_batch enable row level security;
alter table commissioning_reading enable row level security;
create policy commissioning_batch_select on commissioning_batch for select using (firm_id = current_firm_id());
create policy commissioning_reading_select on commissioning_reading for select using (firm_id = current_firm_id());
revoke all on commissioning_batch, commissioning_reading from anon;
revoke insert, update, delete, truncate, trigger, references on commissioning_batch, commissioning_reading from authenticated;
create trigger commissioning_batch_append_only before update or delete on commissioning_batch for each row execute function review_append_only();
create trigger commissioning_reading_append_only before update or delete on commissioning_reading for each row execute function review_append_only();
create function commissioning_batch_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (new.firm_id, new.revision_id, 'commissioning_imported', jsonb_build_object('batch', new.id, 'sha256', new.file_sha256, 'tolerance_pct', new.tolerance_pct, 'counts', new.counts, 'by', new.imported_by));
  return new;
end $$;
create trigger commissioning_batch_audit after insert on commissioning_batch for each row execute function commissioning_batch_audit();
