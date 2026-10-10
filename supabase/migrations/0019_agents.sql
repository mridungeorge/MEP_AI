-- Phase 4b: runtime agents. An agent never writes a result, a review or a sign-off: it can only leave NOTES (draft cards, questions, fix
-- hypotheses, flags, risks, explanations) that humans read, and every tool call it makes, allowed or refused, is recorded.

create table agent_note (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  agent text not null check (agent in ('designer', 'adversarial_checker', 'compliance_risk')),
  kind text not null check (kind in ('spec_card_draft', 'clarifying_question', 'fix_hypothesis', 'flag', 'risk', 'explanation')),
  skill text check (skill is null or skill ~ '^[a-z][a-z0-9-]{1,40}$'),
  rule_result_id uuid,
  severity text check (severity is null or severity in ('info', 'low', 'medium', 'high')),
  body text not null check (length(body) between 1 and 4000),
  data jsonb not null default '{}',
  status text not null default 'open' check (status in ('open', 'answered', 'dismissed')),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  resolved_by uuid,
  resolved_at timestamptz,
  constraint agent_note_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint agent_note_result_fk foreign key (rule_result_id, firm_id) references rule_result (id, firm_id),
  constraint agent_note_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  constraint agent_note_resolver_fk foreign key (resolved_by, firm_id) references app_user (id, firm_id),
  -- who may write which kind: the checker and the risk agent write only flags and risks
  check (agent <> 'adversarial_checker' or kind = 'flag'),
  check (agent <> 'compliance_risk' or kind = 'risk'),
  check (kind not in ('flag', 'risk') or agent in ('adversarial_checker', 'compliance_risk')),
  check (kind <> 'fix_hypothesis' or body like 'Hypothesis: verify%')
);
alter table agent_note enable row level security;
create policy agent_note_firm_select on agent_note for select using (firm_id = current_firm_id());
revoke all on agent_note from anon;
revoke insert, update, delete, truncate, trigger, references on agent_note from authenticated;
create index agent_note_revision_idx on agent_note (revision_id, created_at desc);
create trigger agent_note_audit after insert on agent_note for each row execute function audit_row();

-- A human closes a note (dismiss / answered); nothing else about it may change.
create function agent_note_resolve(p_note uuid, p_status text) returns void language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if p_status not in ('answered', 'dismissed') then raise exception 'unknown status %', p_status; end if;
  update public.agent_note set status = p_status, resolved_by = auth.uid(), resolved_at = now()
    where id = p_note and firm_id = public.current_firm_id() and status = 'open';
  if not found then raise exception 'no such open note in your firm' using errcode = '42501'; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    select firm_id, revision_id, 'agent_note_' || p_status, jsonb_build_object('note', id, 'by', auth.uid()) from public.agent_note where id = p_note;
end $$;
revoke all on function agent_note_resolve(uuid, text) from public, anon;
grant execute on function agent_note_resolve(uuid, text) to authenticated;
create function agent_note_immutable() returns trigger language plpgsql as
$$ begin
  if tg_op = 'DELETE' or (to_jsonb(new) - 'status' - 'resolved_by' - 'resolved_at') is distinct from (to_jsonb(old) - 'status' - 'resolved_by' - 'resolved_at')
     or old.status <> 'open' then
    raise exception 'agent notes are append-only; only an open note can be closed';
  end if;
  return new;
end $$;
create trigger agent_note_no_edit before update or delete on agent_note for each row execute function agent_note_immutable();

create table agent_call (
  id bigint generated always as identity primary key,
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  agent text not null,
  tool text not null,
  allowed boolean not null,
  detail text not null default '',
  args jsonb not null default '{}',
  requested_by uuid not null,
  created_at timestamptz not null default now(),
  constraint agent_call_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint agent_call_user_fk foreign key (requested_by, firm_id) references app_user (id, firm_id)
);
alter table agent_call enable row level security;
create policy agent_call_firm_select on agent_call for select using (firm_id = current_firm_id());
revoke all on agent_call from anon;
revoke insert, update, delete, truncate, trigger, references on agent_call from authenticated;
create trigger agent_call_append_only before update or delete on agent_call for each row execute function review_append_only();
create trigger agent_call_audit after insert on agent_call for each row execute function audit_row();
