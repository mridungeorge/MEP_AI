-- Phase 4a.1 (pilot readiness): signer independence as a firm setting, accepted FAILs, share sessions, registration audit.

-- ============================================================================================================================
-- 1. Signer independence: strict (default; three different people) or small_firm (one person may hold several gates).
-- ============================================================================================================================
alter table firm add column signer_mode text not null default 'strict' check (signer_mode in ('strict', 'small_firm'));
-- In small_firm mode a person may ACT in these roles besides their own. Set by the service only (no client can write app_user).
alter table app_user add column also_roles user_role[] not null default '{}';

create function firm_signer_mode() returns text language sql stable security definer set search_path = public, pg_temp as
$$ select f.signer_mode from public.firm f join public.app_user u on u.firm_id = f.id where u.id = auth.uid() $$;

-- The role the caller is ACTING in: their own, or (small_firm only) one of also_roles named in the signed-in request's claims.
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
    from public.app_user u join public.firm f on f.id = u.firm_id where u.id = auth.uid()
$$;

-- Every ledger entry of a small_firm firm carries the notice (set under the chain lock, so it is inside the hash).
create or replace function ledger_event_chain() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare last_seq bigint; last_hash text;
begin
  perform pg_advisory_xact_lock(hashtextextended('ledger:' || new.firm_id::text, 0));
  if exists (select 1 from public.firm where id = new.firm_id and signer_mode = 'small_firm') then
    new.payload := new.payload || jsonb_build_object('independence_notice', 'NOT INDEPENDENTLY CHECKED');
  end if;
  select seq, row_hash into last_seq, last_hash from public.ledger_event where firm_id = new.firm_id order by seq desc limit 1;
  new.seq := coalesce(last_seq, 0) + 1;
  new.prev_hash := coalesce(last_hash, repeat('0', 64));
  new.row_hash := public.ledger_row_hash(new.prev_hash, new.seq, new.id, new.firm_id, new.revision_id, new.kind, new.payload,
                                         new.created_at);
  insert into public.ledger_head (firm_id, seq, row_hash) values (new.firm_id, new.seq, new.row_hash)
    on conflict (firm_id) do update set seq = excluded.seq, row_hash = excluded.row_hash;
  return new;
end $$;

alter table signoff add column signer_mode text not null default 'strict' check (signer_mode in ('strict', 'small_firm'));

-- Changing a firm's mode, or a person's registration number / roles, is itself a ledger entry (these tables are service-written only).
create function firm_setting_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if new.signer_mode is distinct from old.signer_mode then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.id, 'firm_signer_mode_changed', jsonb_build_object('from', old.signer_mode, 'to', new.signer_mode));
  end if;
  return new;
end $$;
create trigger firm_setting_audit after update on firm for each row execute function firm_setting_audit();

create function app_user_audit() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$ begin
  if tg_op = 'INSERT' or (new.registration_no, new.role, new.also_roles) is distinct from (old.registration_no, old.role, old.also_roles) then
    insert into public.ledger_event (firm_id, kind, payload)
      values (new.firm_id, 'app_user_changed', jsonb_build_object(
        'user_id', new.id, 'role', new.role, 'also_roles', new.also_roles, 'registration_no', new.registration_no,
        'previous', case when tg_op = 'UPDATE' then jsonb_build_object('role', old.role, 'also_roles', old.also_roles,
                                                                         'registration_no', old.registration_no) end));
  end if;
  return new;
end $$;
create trigger app_user_audit after insert or update on app_user for each row execute function app_user_audit();

-- ============================================================================================================================
-- 2. Accepted FAIL: a reason category + text, never in bulk, individually acknowledged by the approver at Gate 3.
-- ============================================================================================================================
alter table review add column fail_category text check (fail_category in ('performance_solution', 'rule_disputed', 'out_of_scope')),
  add column fail_reference text check (fail_reference is null or length(fail_reference) <= 500);
alter table review add constraint review_fail_category_only_on_approve check (fail_category is null or (decision = 'approve' and not bulk));
alter table review add constraint review_performance_solution_reference
  check (fail_category is distinct from 'performance_solution' or length(btrim(coalesce(fail_reference, ''))) >= 3);
create or replace view review_latest with (security_invoker = on) as
  select distinct on (rule_result_id) rule_result_id, revision_id, firm_id, user_id, decision, reason, bulk, spot_check, seq, created_at,
         fail_category, fail_reference
    from review order by rule_result_id, seq desc;

create table rule_dispute (
  id bigint generated always as identity primary key,
  firm_id uuid not null references firm(id),
  rule_id text not null,
  revision_id uuid not null,
  rule_result_id uuid not null,
  flagged_by uuid not null,
  reason text not null,
  flagged_at timestamptz not null default now(),
  constraint rule_dispute_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint rule_dispute_result_fk foreign key (rule_result_id, firm_id) references rule_result (id, firm_id),
  constraint rule_dispute_user_fk foreign key (flagged_by, firm_id) references app_user (id, firm_id),
  unique (rule_result_id)
);
alter table rule_dispute enable row level security;
create policy rule_dispute_firm_select on rule_dispute for select using (firm_id = current_firm_id());
revoke all on rule_dispute from anon;
revoke insert, update, delete, truncate, trigger, references on rule_dispute from authenticated;
create trigger rule_dispute_audit after insert on rule_dispute for each row execute function audit_row();

create table fail_ack (
  id bigint generated always as identity primary key,
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  rule_result_id uuid not null,
  user_id uuid not null,
  note text not null check (length(btrim(note)) >= 3 and length(note) <= 2000),
  acknowledged_at timestamptz not null default now(),
  constraint fail_ack_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint fail_ack_result_fk foreign key (rule_result_id, firm_id) references rule_result (id, firm_id),
  constraint fail_ack_user_fk foreign key (user_id, firm_id) references app_user (id, firm_id),
  unique (rule_result_id)
);
alter table fail_ack enable row level security;
create policy fail_ack_firm_select on fail_ack for select using (firm_id = current_firm_id());
revoke all on fail_ack from anon;
revoke insert, update, delete, truncate, trigger, references on fail_ack from authenticated;
create trigger fail_ack_append_only before update or delete on fail_ack for each row execute function review_append_only();
create trigger fail_ack_audit after insert on fail_ack for each row execute function audit_row();

drop function gate2_review(uuid, text, text, uuid);
create function gate2_review(p_result uuid, p_decision text, p_reason text, p_sample uuid default null,
                             p_fail_category text default null, p_fail_reference text default null) returns bigint
language plpgsql security definer set search_path = public, pg_temp as
$$
declare firm uuid; rev uuid; seq_out bigint; smp public.review_sample; outcome text; rid text;
begin
  select revision_id, result::text, rule_id into rev, outcome, rid from public.rule_result
    where id = p_result and firm_id = public.current_firm_id() and current;
  if rev is null then raise exception 'result not found, not current, or not in your firm' using errcode = '42501'; end if;
  firm := public.gate2_context(rev);
  if p_decision not in ('approve', 'reject', 'request_changes') then raise exception 'unknown decision %', p_decision; end if;
  if length(btrim(coalesce(p_reason, ''))) < 3 then raise exception 'a reason is required for every line'; end if;
  if outcome = 'FAIL' and p_decision = 'approve' then
    if p_fail_category is null then
      raise exception 'approving a FAIL needs a reason category (performance_solution, rule_disputed or out_of_scope)';
    end if;
    if length(btrim(p_reason)) < 10 then raise exception 'an accepted FAIL needs an explanation of at least 10 characters'; end if;
    if p_sample is not null then raise exception 'a FAIL is never part of a bulk spot-check'; end if;
  elsif p_fail_category is not null then
    raise exception 'a reason category only applies to approving a FAIL';
  end if;
  if p_sample is not null then
    select * into smp from public.review_sample where id = p_sample and revision_id = rev and firm_id = firm and created_by = auth.uid();
    if smp.id is null or smp.used_at is not null or not (p_result = any (smp.sample_ids)) then
      raise exception 'this result is not part of an open spot-check sample of yours';
    end if;
  elsif exists (select 1 from public.review_sample where revision_id = rev and used_at is null and p_result = any (sample_ids)) then
    raise exception 'this result is in an open spot-check sample: decide it as part of the sample';
  end if;
  insert into public.review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason, spot_check, sample_id,
                             fail_category, fail_reference)
    values (firm, p_result, rev, 'gate2', auth.uid(), p_decision, btrim(p_reason), p_sample is not null, p_sample,
            p_fail_category, nullif(btrim(coalesce(p_fail_reference, '')), ''))
    returning seq into seq_out;
  if p_fail_category = 'rule_disputed' then
    insert into public.rule_dispute (firm_id, rule_id, revision_id, rule_result_id, flagged_by, reason)
      values (firm, rid, rev, p_result, auth.uid(), btrim(p_reason)) on conflict (rule_result_id) do nothing;
  end if;
  return seq_out;
end $$;
revoke all on function gate2_review(uuid, text, text, uuid, text, text) from public, anon;
grant execute on function gate2_review(uuid, text, text, uuid, text, text) to authenticated;

-- The approver acknowledges each accepted FAIL individually (never in bulk) before Gate 3.
create function gate3_acknowledge_fail(p_result uuid, p_note text) returns bigint language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id(); rev uuid; cat text; mode text := public.firm_signer_mode(); id_out bigint;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'approver' then
    raise exception 'only an approver acknowledges an accepted FAIL' using errcode = '42501';
  end if;
  select rr.revision_id into rev from public.rule_result rr where rr.id = p_result and rr.firm_id = firm and rr.current;
  if rev is null then raise exception 'result not found, not current, or not in your firm' using errcode = '42501'; end if;
  perform 1 from public.revision where id = rev and firm_id = firm for share;
  if not exists (select 1 from public.signoff where revision_id = rev and gate = 'gate2') then
    raise exception 'Gate 2 has not been signed';
  end if;
  if exists (select 1 from public.signoff where revision_id = rev and gate = 'gate3') then raise exception 'Gate 3 is already signed'; end if;
  select fail_category into cat from public.review_latest where rule_result_id = p_result and decision = 'approve';
  if cat is null then raise exception 'this result is not an accepted FAIL'; end if;
  if mode = 'strict' and exists (select 1 from public.review where revision_id = rev and user_id = auth.uid()) then
    raise exception 'you reviewed this revision at Gate 2: the approver must be someone else';
  end if;
  insert into public.fail_ack (firm_id, revision_id, rule_result_id, user_id, note)
    values (firm, rev, p_result, auth.uid(), btrim(p_note)) returning id into id_out;
  return id_out;
end $$;
revoke all on function gate3_acknowledge_fail(uuid, text) from public, anon;
grant execute on function gate3_acknowledge_fail(uuid, text) to authenticated;

-- ============================================================================================================================
-- 1b. gate context and sign_gate honour the firm's signer mode.
-- ============================================================================================================================
create or replace function gate2_context(p_revision uuid) returns uuid language plpgsql volatile security definer
  set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id();
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'checker' then
    raise exception 'only a checker reviews at Gate 2' using errcode = '42501';
  end if;
  perform 1 from public.revision where id = p_revision and firm_id = firm for share;
  if not found then raise exception 'revision not found in your firm' using errcode = '42501'; end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = firm and frozen_at is not null) then
    raise exception 'the revision must be frozen (Gate 1 signed) before Gate 2';
  end if;
  if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate1') then
    raise exception 'Gate 1 has not been signed';
  end if;
  if public.firm_signer_mode() = 'strict'
     and exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate1' and user_id = auth.uid()) then
    raise exception 'you signed Gate 1 of this revision: someone else must review it';
  end if;
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
    raise exception 'Gate 2 is already signed';
  end if;
  return firm;
end $$;

create or replace function freeze_revision(p_revision uuid) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare head_seq bigint; head_hash text; ran text[]; parent uuid;
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'designer' then
    raise exception 'only a designer freezes a revision' using errcode = '42501';
  end if;
  select parent_revision_id into parent from public.revision
    where id = p_revision and firm_id = public.current_firm_id() for update;
  if not found then raise exception 'revision not found in your firm' using errcode = '42501'; end if;
  select array_agg(distinct inputs_hash) into ran from public.rule_result
    where revision_id = p_revision and firm_id = public.current_firm_id() and current;
  if ran is null then raise exception 'run the rules before freezing: there are no current results'; end if;
  if ran <> array[public.live_inputs_hash(p_revision)] then
    raise exception 'the inputs changed since the rules were last run: run them again before freezing';
  end if;
  if parent is not null and not exists (select 1 from public.revision_diff where revision_id = p_revision) then
    raise exception 'confirm the revision diff before freezing';
  end if;
  update public.revision set frozen_at = now(), status = 'frozen'
    where id = p_revision and firm_id = public.current_firm_id() and frozen_at is null;
  if not found then raise exception 'revision is already frozen'; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'revision_frozen',
            jsonb_build_object('frozen_by', auth.uid(), 'role', 'designer'));
  select seq, row_hash into head_seq, head_hash from public.ledger_event
    where firm_id = public.current_firm_id() order by seq desc limit 1;
  insert into public.signoff (firm_id, revision_id, gate, user_id, signer_role, anchor_seq, anchor_hash, statement, signer_mode)
    values (public.current_firm_id(), p_revision, 'gate1', auth.uid(), 'designer', head_seq, head_hash,
            jsonb_build_object('attests', 'inputs confirmed at Gate 1; revision frozen'), public.firm_signer_mode());
end $$;

create or replace function sign_gate(p_revision uuid, p_gate gate, p_registration text default null) returns bigint
language plpgsql security definer set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id(); me public.app_user; head_seq bigint; head_hash text; n int; open_n int; ack_n int;
        attest jsonb; sid uuid; reg text; acting public.user_role := public.current_user_role(); mode text := public.firm_signer_mode();
begin
  select * into me from public.app_user where id = auth.uid() and firm_id = firm;
  if me.id is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if p_gate = 'gate1' then raise exception 'Gate 1 is signed by freezing the revision'; end if;
  if acting is distinct from public.gate_role(p_gate) then
    raise exception 'only a % signs %', public.gate_role(p_gate), p_gate using errcode = '42501';
  end if;
  perform 1 from public.revision where id = p_revision and firm_id = firm for update;
  if not found then raise exception 'revision not found in your firm' using errcode = '42501'; end if;
  if not exists (select 1 from public.revision where id = p_revision and frozen_at is not null) then
    raise exception 'the revision must be frozen before it is signed';
  end if;
  if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate1') then
    raise exception 'Gate 1 has not been signed';
  end if;
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = p_gate) then
    raise exception '% is already signed by another signer (one signer per gate)', p_gate;
  end if;
  if mode = 'strict' and exists (select 1 from public.signoff where revision_id = p_revision and user_id = me.id) then
    raise exception 'you already signed another gate of this revision: each gate needs a different person';
  end if;
  select count(*) into n from public.rule_result where revision_id = p_revision and firm_id = firm and current;
  if n = 0 then raise exception 'there are no current results to sign'; end if;
  if p_gate = 'gate2' then
    if not exists (select 1 from public.review where revision_id = p_revision and user_id = me.id) then
      raise exception 'you have not reviewed anything on this revision: a Gate 2 signer must have done the review';
    end if;
    select count(*) into open_n from public.rule_result rr
      where rr.revision_id = p_revision and rr.firm_id = firm and rr.current
        and (rr.stale or rr.review_class is null
             or coalesce((select v.decision from public.review v where v.rule_result_id = rr.id order by v.seq desc limit 1), '') <> 'approve'
             or coalesce((select v.bulk from public.review v where v.rule_result_id = rr.id order by v.seq desc limit 1), false)
                and rr.review_class is distinct from 'clean_pass');
    if open_n > 0 then raise exception '% result(s) are stale, unclassified, unreviewed or not approved', open_n; end if;
    attest := jsonb_build_object('attests', 'every current result reviewed and approved', 'results', n);
  else
    if mode = 'strict' and exists (select 1 from public.review where revision_id = p_revision and user_id = me.id) then
      raise exception 'you reviewed this revision at Gate 2: the approver must be someone else';
    end if;
    if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
      raise exception 'Gate 2 has not been signed';
    end if;
    select count(*) into ack_n from public.review_latest v
      where v.revision_id = p_revision and v.decision = 'approve' and v.fail_category is not null
        and not exists (select 1 from public.fail_ack a where a.rule_result_id = v.rule_result_id)
        and exists (select 1 from public.rule_result rr where rr.id = v.rule_result_id and rr.current);
    if ack_n > 0 then
      raise exception '% accepted FAIL(s) need the approver''s individual acknowledgement before Gate 3', ack_n;
    end if;
    if me.registration_no is null or length(btrim(me.registration_no)) < 3 then
      raise exception 'the approver has no registration number on file';
    end if;
    reg := btrim(coalesce(p_registration, ''));
    if lower(reg) <> lower(btrim(me.registration_no)) then
      raise exception 'the registration number given does not match the one on file';
    end if;
    attest := jsonb_build_object('attests', 'approved for issue', 'results', n);
  end if;
  select seq, row_hash into head_seq, head_hash from public.ledger_event where firm_id = firm order by seq desc limit 1;
  insert into public.signoff (firm_id, revision_id, gate, user_id, signer_role, registration_no, anchor_seq, anchor_hash, statement,
                              signer_mode)
    values (firm, p_revision, p_gate, me.id, acting, case when p_gate = 'gate3' then btrim(me.registration_no) end,
            head_seq, head_hash, attest, mode)
    returning id into sid;
  return head_seq;
end $$;

-- ============================================================================================================================
-- 4. Share sessions: the link token is exchanged ONCE (POST) for a short-lived, read-only session; only hashes are stored.
-- ============================================================================================================================
create table share_session (
  id_hash text primary key check (id_hash ~ '^[0-9a-f]{64}$'),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  link_hash text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null,
  constraint share_session_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  check (expires_at > created_at and expires_at <= created_at + interval '1 hour')
);
alter table share_session enable row level security;
revoke all on share_session from anon, authenticated;

-- Exchange: validates the link, logs the opening (counted as a view), returns the new session's scope.
create function exchange_share_link(p_token_hash text, p_session_hash text, p_minutes int, p_client text default null)
returns table (firm_id uuid, revision_id uuid) language plpgsql security definer set search_path = public, pg_temp as
$$
declare l public.ledger_link;
begin
  if p_minutes < 1 or p_minutes > 60 then raise exception 'a share session lasts 1 to 60 minutes'; end if;
  select * into l from public.ledger_link where token = p_token_hash for update;
  if l.token is null or l.revoked_at is not null or l.expires_at <= now() then return; end if;
  delete from public.share_session where expires_at < now() - interval '1 day';
  update public.ledger_link set views = views + 1, last_viewed_at = now() where token = p_token_hash;
  insert into public.share_session (id_hash, firm_id, revision_id, link_hash, expires_at)
    values (p_session_hash, l.firm_id, l.revision_id, p_token_hash,
            least(now() + make_interval(mins => p_minutes), l.expires_at));
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (l.firm_id, l.revision_id, 'share_link_viewed',
            jsonb_build_object('link_created_by', l.created_by, 'expires_at', l.expires_at, 'view', l.views + 1,
                               'client', left(p_client, 120), 'what', 'exchange'));
  return query select l.firm_id, l.revision_id;
end $$;

-- Reads with a session: valid only while the session AND its link are valid; each read is logged.
create function read_share_session(p_session_hash text, p_what text) returns table (firm_id uuid, revision_id uuid)
language plpgsql security definer set search_path = public, pg_temp as
$$
declare s public.share_session; l public.ledger_link;
begin
  select * into s from public.share_session where id_hash = p_session_hash;
  if s.id_hash is null or s.expires_at <= now() then return; end if;
  select * into l from public.ledger_link where token = s.link_hash;
  if l.token is null or l.revoked_at is not null or l.expires_at <= now() then return; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (s.firm_id, s.revision_id, 'share_link_read', jsonb_build_object('what', left(p_what, 40), 'link_created_by', l.created_by));
  return query select s.firm_id, s.revision_id;
end $$;

drop function open_share_link(text, text);
revoke all on function exchange_share_link(text, text, int, text), read_share_session(text, text) from public, anon, authenticated;
revoke all on function firm_signer_mode(), firm_setting_audit(), app_user_audit() from public, anon;
grant execute on function firm_signer_mode() to authenticated;
