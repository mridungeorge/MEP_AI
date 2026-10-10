-- Phase 7.2: Performance Solution pathway. A designer marks, per failed result, whether the design is pursued by the DTS (deemed-to-satisfy) pathway or as a
-- Performance Solution, and attaches the engineer's own simulation / assessment results as EVIDENCE. Evidence is never a rule result and is never read by the
-- engine; it exists so the engineer's reasoning travels with the revision.

create table result_pathway (
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  subject_id text not null,
  rule_id text not null,
  pathway text not null check (pathway in ('DTS', 'PERFORMANCE_SOLUTION')),
  note text check (note is null or length(note) <= 1000),
  set_by uuid not null,
  set_at timestamptz not null default now(),
  primary key (revision_id, subject_id, rule_id),
  constraint result_pathway_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint result_pathway_user_fk foreign key (set_by, firm_id) references app_user (id, firm_id)
);
alter table result_pathway enable row level security;
create policy result_pathway_select on result_pathway for select using (firm_id = current_firm_id());
revoke all on result_pathway from anon;
revoke insert, update, delete, truncate, trigger, references on result_pathway from authenticated;
create trigger result_pathway_frozen before insert or update or delete on result_pathway for each row execute function reject_if_revision_frozen();

create table perf_evidence (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  subject_id text not null,
  rule_id text not null,
  title text not null check (length(title) between 3 and 200),
  tool text check (tool is null or length(tool) <= 120),
  description text check (description is null or length(description) <= 4000),
  metrics jsonb not null default '[]',
  provenance text not null default 'engineer_supplied' check (provenance = 'engineer_supplied'),
  file_name text check (file_name is null or length(file_name) <= 200),
  file_sha256 text check (file_sha256 is null or file_sha256 ~ '^[0-9a-f]{64}$'),
  file_content bytea check (file_content is null or length(file_content) <= 10485760),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  constraint perf_evidence_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint perf_evidence_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  check ((file_content is null) = (file_sha256 is null))
);
create index perf_evidence_revision on perf_evidence (revision_id, subject_id, rule_id);
alter table perf_evidence enable row level security;
create policy perf_evidence_select on perf_evidence for select using (firm_id = current_firm_id());
revoke all on perf_evidence from anon;
revoke select on perf_evidence from authenticated;
grant select (id, firm_id, revision_id, subject_id, rule_id, title, tool, description, metrics, provenance, file_name, file_sha256, created_by, created_at) on perf_evidence to authenticated;
revoke insert, update, delete, truncate, trigger, references on perf_evidence from authenticated;
create trigger perf_evidence_frozen before insert on perf_evidence for each row execute function reject_if_revision_frozen();
create trigger perf_evidence_append_only before update or delete on perf_evidence for each row execute function review_append_only();

create function perf_evidence_audit_fn() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (new.firm_id, new.revision_id, 'performance_evidence_recorded', jsonb_build_object('evidence', new.id, 'subject', new.subject_id, 'rule', new.rule_id,
            'title', new.title, 'file_sha256', new.file_sha256, 'by', new.created_by));
  return new;
end $$;

create trigger perf_evidence_audit after insert on perf_evidence for each row execute function perf_evidence_audit_fn();

create function result_pathway_audit_fn() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (new.firm_id, new.revision_id, 'result_pathway_set', jsonb_build_object('subject', new.subject_id, 'rule', new.rule_id, 'pathway', new.pathway, 'by', new.set_by));
  return new;
end $$;
create trigger result_pathway_audit after insert or update on result_pathway for each row execute function result_pathway_audit_fn();

-- the designer's two writes, as the user (the service never writes them for someone else)
create function perf_set_pathway(p_revision uuid, p_subject text, p_rule text, p_pathway text, p_note text default null) returns void
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id();
begin
  if auth.uid() is null or f is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer chooses a pathway' using errcode = '42501';
  end if;
  if not exists (select 1 from public.rule_result where revision_id = p_revision and firm_id = f and subject_id = p_subject and rule_id = p_rule and current) then
    raise exception 'there is no current result for that system and rule';
  end if;
  insert into public.result_pathway (firm_id, revision_id, subject_id, rule_id, pathway, note, set_by)
    values (f, p_revision, p_subject, p_rule, p_pathway, p_note, auth.uid())
    on conflict (revision_id, subject_id, rule_id) do update set pathway = excluded.pathway, note = excluded.note, set_by = excluded.set_by, set_at = now();
end $$;

create function perf_add_evidence(p_revision uuid, p_subject text, p_rule text, p_title text, p_tool text, p_description text, p_metrics jsonb,
                                  p_file_name text, p_file bytea, p_sha256 text) returns uuid
language plpgsql security definer set search_path = public, pg_temp as
$$
declare f uuid := public.current_firm_id(); eid uuid;
begin
  if auth.uid() is null or f is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer records evidence' using errcode = '42501';
  end if;
  if not exists (select 1 from public.rule_result where revision_id = p_revision and firm_id = f and subject_id = p_subject and rule_id = p_rule and current) then
    raise exception 'there is no current result for that system and rule';
  end if;
  insert into public.perf_evidence (firm_id, revision_id, subject_id, rule_id, title, tool, description, metrics, file_name, file_sha256, file_content, created_by)
    values (f, p_revision, p_subject, p_rule, btrim(p_title), p_tool, p_description, coalesce(p_metrics, '[]'), p_file_name, p_sha256, p_file, auth.uid())
    returning id into eid;
  return eid;
end $$;

create function perf_evidence_file(p_id uuid) returns table (file_name text, file_content bytea)
language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  if auth.uid() is null or public.current_firm_id() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  return query select e.file_name, e.file_content from public.perf_evidence e where e.id = p_id and e.firm_id = public.current_firm_id() and e.file_content is not null;
end $$;
revoke all on function perf_set_pathway(uuid, text, text, text, text), perf_add_evidence(uuid, text, text, text, text, text, jsonb, text, bytea, text), perf_evidence_file(uuid)
  from public, anon;
grant execute on function perf_set_pathway(uuid, text, text, text, text), perf_add_evidence(uuid, text, text, text, text, text, jsonb, text, bytea, text), perf_evidence_file(uuid)
  to authenticated;
