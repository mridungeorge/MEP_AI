-- Phase 4a: Gate 2 review, the sign-off state machine, the ledger hash chain with audit triggers, and certifier share links.
-- Nothing here approves a rule or changes a rule file. A client never writes a review, a sign-off or a ledger row directly: the only
-- doors are the security-definer functions below, each of which checks the role, the gate order and the state of the revision.

-- ============================================================================================================================
-- 1. The ledger hash chain. Per firm, rows get a sequence number under a lock, and each row's hash covers the previous hash, so
--    changing, removing or re-ordering any row (or truncating the tail past a sign-off anchor) is detectable by verify_ledger().
-- ============================================================================================================================
alter table ledger_event add column seq bigint, add column prev_hash text, add column row_hash text;

create function ledger_row_hash(p_prev text, p_seq bigint, p_id bigint, p_firm uuid, p_revision uuid, p_kind text,
                                p_payload jsonb, p_at timestamptz) returns text language sql immutable as
$$ select encode(sha256(convert_to(
     p_prev || '|' || p_seq || '|' || p_id || '|' || p_firm || '|' || coalesce(p_revision::text, '') || '|' || p_kind || '|'
     || p_payload::text || '|' || to_char(p_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'), 'UTF8')), 'hex') $$;

-- Backfill what exists (the immutability trigger is bypassed for this one statement by a session flag it honours).
create or replace function ledger_event_immutable() returns trigger language plpgsql as
$$ begin
  if tg_op = 'UPDATE' and current_setting('mep.ledger_backfill', true) = 'on' then return new; end if;
  raise exception 'ledger_event is append-only';
end $$;

do $$
declare r record; prev text; n bigint; cur uuid := null;
begin
  perform set_config('mep.ledger_backfill', 'on', true);
  for r in select * from ledger_event order by firm_id, id loop
    if cur is distinct from r.firm_id then cur := r.firm_id; prev := repeat('0', 64); n := 0; end if;
    n := n + 1;
    update ledger_event set seq = n, prev_hash = prev,
      row_hash = ledger_row_hash(prev, n, r.id, r.firm_id, r.revision_id, r.kind, r.payload, r.created_at)
      where id = r.id
      returning row_hash into prev;
  end loop;
  perform set_config('mep.ledger_backfill', 'off', true);
end $$;

alter table ledger_event alter column seq set not null, alter column prev_hash set not null, alter column row_hash set not null;
alter table ledger_event add constraint ledger_event_firm_seq_uq unique (firm_id, seq);
alter table ledger_event add constraint ledger_event_hash_shape check (row_hash ~ '^[0-9a-f]{64}$' and prev_hash ~ '^[0-9a-f]{64}$');

create function ledger_event_chain() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare last_seq bigint; last_hash text;
begin
  perform pg_advisory_xact_lock(hashtextextended('ledger:' || new.firm_id::text, 0));
  select seq, row_hash into last_seq, last_hash from public.ledger_event where firm_id = new.firm_id order by seq desc limit 1;
  new.seq := coalesce(last_seq, 0) + 1;                      -- whatever the caller supplied is ignored
  new.prev_hash := coalesce(last_hash, repeat('0', 64));
  new.row_hash := public.ledger_row_hash(new.prev_hash, new.seq, new.id, new.firm_id, new.revision_id, new.kind, new.payload,
                                         new.created_at);
  return new;
end $$;
create trigger ledger_event_chain before insert on ledger_event for each row execute function ledger_event_chain();

-- The check: every row is in sequence, links to the one before, hashes to what it says, and every sign-off's anchor still holds.
create function verify_ledger(p_firm uuid)
returns table (ok boolean, checked bigint, broken_seq bigint, reason text, head_seq bigint, head_hash text)
language plpgsql stable security definer set search_path = public, pg_temp as
$$
declare r record; prev text := repeat('0', 64); expected bigint := 1; n bigint := 0; s record; anchor text;
begin
  if current_setting('role', true) in ('anon', 'authenticated') and p_firm is distinct from public.current_firm_id() then
    raise exception 'ledger of another firm' using errcode = '42501';
  end if;
  for r in select * from public.ledger_event where firm_id = p_firm order by seq loop
    n := n + 1;
    if r.seq <> expected then
      return query select false, n, expected, 'a row is missing or out of sequence', null::bigint, null::text; return;
    end if;
    if r.prev_hash <> prev then
      return query select false, n, r.seq, 'row does not link to the one before it', null::bigint, null::text; return;
    end if;
    if r.row_hash <> public.ledger_row_hash(r.prev_hash, r.seq, r.id, r.firm_id, r.revision_id, r.kind, r.payload, r.created_at) then
      return query select false, n, r.seq, 'row content does not match its hash', null::bigint, null::text; return;
    end if;
    prev := r.row_hash; expected := expected + 1;
  end loop;
  for s in select * from public.signoff where firm_id = p_firm and anchor_seq is not null loop
    select row_hash into anchor from public.ledger_event where firm_id = p_firm and seq = s.anchor_seq;
    if anchor is distinct from s.anchor_hash then
      return query select false, n, s.anchor_seq, 'a sign-off''s ledger anchor no longer holds (rows removed or rewritten)', null::bigint, null::text;
      return;
    end if;
  end loop;
  return query select true, n, null::bigint, null::text, case when n = 0 then null else expected - 1 end,
                      case when n = 0 then null else prev end;
end $$;
revoke all on function verify_ledger(uuid) from public, anon;
grant execute on function verify_ledger(uuid) to authenticated;

-- ============================================================================================================================
-- 2. Reviews (Gate 2): append-only, written only by gate2_review / gate2_bulk_approve.
-- ============================================================================================================================
alter table review
  add column revision_id uuid, add column seq bigint generated always as identity,
  add column created_at timestamptz not null default now(),
  add column bulk boolean not null default false, add column spot_check boolean not null default false,
  add column sample_id uuid;
update review set revision_id = (select rr.revision_id from rule_result rr where rr.id = review.rule_result_id);
alter table review alter column revision_id set not null;
alter table review add constraint review_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table review add constraint review_reason_required check (bulk or length(btrim(coalesce(reason, ''))) >= 3) not valid;
alter table review add constraint review_reason_size check (reason is null or length(reason) <= 2000);
create index review_result_idx on review (rule_result_id, seq);

create function review_append_only() returns trigger language plpgsql as
$$ begin raise exception 'reviews are append-only: a new decision supersedes the old one'; end $$;
create trigger review_no_update before update or delete on review for each row execute function review_append_only();
create trigger review_no_truncate before truncate on review for each statement execute function review_append_only();

drop policy review_insert on review;
drop policy signoff_insert on signoff;
revoke insert, update, delete on review, signoff from authenticated;

-- The latest decision per result.
create view review_latest with (security_invoker = on) as
  select distinct on (rule_result_id) rule_result_id, revision_id, firm_id, user_id, decision, reason, bulk, spot_check, seq, created_at
    from review order by rule_result_id, seq desc;
revoke all on review_latest from anon;

-- ============================================================================================================================
-- 3. Sign-offs: columns for what was attested, immutable once written.
-- ============================================================================================================================
alter table signoff
  add column signer_role user_role, add column registration_no text, add column anchor_seq bigint, add column anchor_hash text,
  add column statement jsonb not null default '{}';
alter table signoff add constraint signoff_gate3_registration check (gate <> 'gate3' or length(btrim(coalesce(registration_no, ''))) >= 3);
create trigger signoff_append_only before update or delete on signoff for each row execute function review_append_only();
create trigger signoff_no_truncate before truncate on signoff for each statement execute function review_append_only();

-- A signed revision is immutable: its results, classes and reviews cannot change any more.
create function signed_revision_immutable() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid; firm uuid;
begin
  rev := coalesce(new.revision_id, old.revision_id); firm := coalesce(new.firm_id, old.firm_id);
  if exists (select 1 from public.signoff where revision_id = rev and firm_id = firm and gate in ('gate2', 'gate3')) then
    raise exception 'revision is signed and immutable';
  end if;
  return coalesce(new, old);
end $$;
create trigger rule_result_signed_immutable before insert or update or delete on rule_result
  for each row execute function signed_revision_immutable();
create trigger review_signed_immutable before insert on review for each row execute function signed_revision_immutable();

-- Freezing is the designer's Gate 1 sign-off.
create or replace function freeze_revision(p_revision uuid) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare head_seq bigint; head_hash text;
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
  select seq, row_hash into head_seq, head_hash from public.ledger_event
    where firm_id = public.current_firm_id() order by seq desc limit 1;
  insert into public.signoff (firm_id, revision_id, gate, user_id, signer_role, anchor_seq, anchor_hash, statement)
    values (public.current_firm_id(), p_revision, 'gate1', auth.uid(), 'designer', head_seq, head_hash,
            jsonb_build_object('attests', 'inputs confirmed at Gate 1; revision frozen'));
end $$;

-- Revisions frozen before this migration: their Gate 1 sign-off is the freeze recorded in the ledger.
insert into signoff (firm_id, revision_id, gate, user_id, signed_at, signer_role, statement)
  select e.firm_id, e.revision_id, 'gate1', (e.payload ->> 'frozen_by')::uuid, e.created_at, 'designer',
         '{"attests": "backfilled from the ledger freeze record"}'::jsonb
    from ledger_event e
   where e.kind = 'revision_frozen' and e.revision_id is not null and e.payload ? 'frozen_by'
     and exists (select 1 from app_user u where u.id = (e.payload ->> 'frozen_by')::uuid and u.firm_id = e.firm_id)
     and not exists (select 1 from signoff s where s.revision_id = e.revision_id and s.gate = 'gate1');

-- ============================================================================================================================
-- 4. Gate 2: line-by-line decisions, and bulk approval of clean passes behind a random spot-check.
-- ============================================================================================================================
create table review_sample (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  created_by uuid not null,
  created_at timestamptz not null default now(),
  candidate_ids uuid[] not null,          -- the clean passes still unreviewed when the sample was drawn
  sample_ids uuid[] not null,             -- the ones the checker must examine individually first
  used_at timestamptz,
  constraint review_sample_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint review_sample_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  check (sample_ids <@ candidate_ids)
);
alter table review_sample enable row level security;
create policy review_sample_firm_select on review_sample for select using (firm_id = current_firm_id());
revoke all on review_sample from anon;
revoke insert, update, delete, truncate, trigger, references on review_sample from authenticated;

create function gate2_context(p_revision uuid) returns uuid language plpgsql stable security definer
  set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id();
begin
  if auth.uid() is null or public.current_user_role() is distinct from 'checker' then
    raise exception 'only a checker reviews at Gate 2' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = firm) then
    raise exception 'revision not found in your firm' using errcode = '42501';
  end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = firm and frozen_at is not null) then
    raise exception 'the revision must be frozen (Gate 1 signed) before Gate 2';
  end if;
  if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate1') then
    raise exception 'Gate 1 has not been signed';
  end if;
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
    raise exception 'Gate 2 is already signed';
  end if;
  return firm;
end $$;

create function gate2_review(p_result uuid, p_decision text, p_reason text, p_sample uuid default null) returns bigint
language plpgsql security definer set search_path = public, pg_temp as
$$
declare firm uuid; rev uuid; seq_out bigint; smp public.review_sample;
begin
  select revision_id into rev from public.rule_result where id = p_result and firm_id = public.current_firm_id() and current;
  if rev is null then raise exception 'result not found, not current, or not in your firm' using errcode = '42501'; end if;
  firm := public.gate2_context(rev);
  if p_decision not in ('approve', 'reject', 'request_changes') then raise exception 'unknown decision %', p_decision; end if;
  if length(btrim(coalesce(p_reason, ''))) < 3 then raise exception 'a reason is required for every line'; end if;
  if p_sample is not null then
    select * into smp from public.review_sample where id = p_sample and revision_id = rev and firm_id = firm and created_by = auth.uid();
    if smp.id is null or smp.used_at is not null or not (p_result = any (smp.sample_ids)) then
      raise exception 'this result is not part of an open spot-check sample of yours';
    end if;
  end if;
  insert into public.review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason, spot_check, sample_id)
    values (firm, p_result, rev, 'gate2', auth.uid(), p_decision, btrim(p_reason), p_sample is not null, p_sample)
    returning seq into seq_out;
  return seq_out;
end $$;

-- Draw the random sample of unreviewed clean passes: at least 3 (or all, if fewer) and at least 10 per cent.
create function gate2_prepare_bulk(p_revision uuid) returns uuid language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare firm uuid; cand uuid[]; smp uuid[]; n int; k int; sid uuid;
begin
  firm := public.gate2_context(p_revision);
  if exists (select 1 from public.rule_result where revision_id = p_revision and firm_id = firm and current and review_class is null) then
    raise exception 'results have not been classified';
  end if;
  select coalesce(array_agg(rr.id order by rr.id), '{}') into cand from public.rule_result rr
    where rr.revision_id = p_revision and rr.firm_id = firm and rr.current and rr.review_class = 'clean_pass' and not rr.stale
      and not exists (select 1 from public.review v where v.rule_result_id = rr.id);
  n := coalesce(array_length(cand, 1), 0);
  if n = 0 then raise exception 'there are no unreviewed clean passes'; end if;
  k := least(n, greatest(3, ceil(n * 0.10)::int));
  select array_agg(id) into smp from (select unnest(cand) as id order by gen_random_uuid() limit k) s;
  insert into public.review_sample (firm_id, revision_id, created_by, candidate_ids, sample_ids)
    values (firm, p_revision, auth.uid(), cand, smp) returning id into sid;
  return sid;
end $$;

create function gate2_bulk_approve(p_sample uuid) returns int language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare smp public.review_sample; firm uuid; now_cand uuid[]; missing int; bad int; done int;
begin
  select * into smp from public.review_sample where id = p_sample and firm_id = public.current_firm_id() and created_by = auth.uid();
  if smp.id is null then raise exception 'no such spot-check sample of yours'; end if;
  firm := public.gate2_context(smp.revision_id);
  if smp.used_at is not null then raise exception 'this sample was already used'; end if;
  select coalesce(array_agg(rr.id order by rr.id), '{}') into now_cand from public.rule_result rr
    where rr.revision_id = smp.revision_id and rr.firm_id = firm and rr.current and rr.review_class = 'clean_pass' and not rr.stale
      and (not exists (select 1 from public.review v where v.rule_result_id = rr.id)
           or rr.id = any (smp.sample_ids));
  if not (smp.candidate_ids <@ now_cand and now_cand <@ smp.candidate_ids) then
    raise exception 'the set of clean passes changed since the sample was drawn: draw a new sample';
  end if;
  select count(*) into missing from unnest(smp.sample_ids) s(id)
    where not exists (select 1 from public.review v where v.rule_result_id = s.id and v.sample_id = smp.id
                        and v.user_id = auth.uid());
  if missing > 0 then raise exception '% sampled result(s) have not been examined yet', missing; end if;
  select count(*) into bad from unnest(smp.sample_ids) s(id)
    where (select v.decision from public.review v where v.rule_result_id = s.id order by v.seq desc limit 1) <> 'approve';
  if bad > 0 then
    raise exception 'the spot-check found % problem(s): review every clean pass line by line', bad;
  end if;
  insert into public.review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason, bulk, sample_id)
    select firm, c.id, smp.revision_id, 'gate2', auth.uid(), 'approve',
           'bulk approval of a clean pass after a random spot-check of ' || cardinality(smp.sample_ids) || ' of '
             || cardinality(smp.candidate_ids), true, smp.id
      from unnest(smp.candidate_ids) c(id)
     where not exists (select 1 from public.review v where v.rule_result_id = c.id);
  get diagnostics done = row_count;
  update public.review_sample set used_at = now() where id = smp.id;   -- review_sample has no update policy; this runs as owner
  return done;
end $$;

-- ============================================================================================================================
-- 5. Sign-off state machine for Gate 2 and Gate 3 (Gate 1 is the freeze).
-- ============================================================================================================================
create function sign_gate(p_revision uuid, p_gate gate, p_registration text default null) returns bigint
language plpgsql security definer set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id(); me public.app_user; head_seq bigint; head_hash text; n int; open_n int;
        attest jsonb; sid uuid; reg text;
begin
  select * into me from public.app_user where id = auth.uid() and firm_id = firm;
  if me.id is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if p_gate = 'gate1' then raise exception 'Gate 1 is signed by freezing the revision'; end if;
  if me.role is distinct from public.gate_role(p_gate) then
    raise exception 'only a % signs %', public.gate_role(p_gate), p_gate using errcode = '42501';
  end if;
  perform 1 from public.revision where id = p_revision and firm_id = firm for update;     -- serialise signers of one revision
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
  select count(*) into n from public.rule_result where revision_id = p_revision and firm_id = firm and current;
  if n = 0 then raise exception 'there are no current results to sign'; end if;
  if p_gate = 'gate2' then
    select count(*) into open_n from public.rule_result rr
      where rr.revision_id = p_revision and rr.firm_id = firm and rr.current
        and (rr.stale or rr.review_class is null
             or coalesce((select v.decision from public.review v where v.rule_result_id = rr.id order by v.seq desc limit 1), '') <> 'approve');
    if open_n > 0 then raise exception '% result(s) are stale, unclassified, unreviewed or not approved', open_n; end if;
    attest := jsonb_build_object('attests', 'every current result reviewed and approved', 'results', n);
  else
    if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
      raise exception 'Gate 2 has not been signed';
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
  insert into public.signoff (firm_id, revision_id, gate, user_id, signer_role, registration_no, anchor_seq, anchor_hash, statement)
    values (firm, p_revision, p_gate, me.id, me.role, case when p_gate = 'gate3' then btrim(me.registration_no) end,
            head_seq, head_hash, attest)
    returning id into sid;
  return head_seq;
end $$;

-- ============================================================================================================================
-- 6. Audit triggers: every review, sign-off, diff confirmation, artifact and share link also lands in the hash-chained ledger.
-- ============================================================================================================================
create function audit_row() returns trigger language plpgsql security definer set search_path = public, pg_temp as
$$
declare j jsonb := to_jsonb(new); rev uuid;
begin
  rev := case when j ? 'revision_id' then (j ->> 'revision_id')::uuid end;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (new.firm_id, rev, tg_table_name || '_recorded', j - 'token' - 'validator');
  return new;
end $$;
create trigger review_audit after insert on review for each row execute function audit_row();
create trigger signoff_audit after insert on signoff for each row execute function audit_row();
create trigger revision_diff_audit after insert on revision_diff for each row execute function audit_row();
create trigger review_sample_audit after insert on review_sample for each row execute function audit_row();
create trigger artifact_audit after insert on artifact for each row execute function audit_row();

-- ============================================================================================================================
-- 7. Certifier share links: the token is shown once and only its hash is stored; time-limited, read-only, every view logged.
-- ============================================================================================================================
alter table ledger_link
  add column created_by uuid, add column created_at timestamptz not null default now(), add column revoked_at timestamptz,
  add column label text, add column views int not null default 0, add column last_viewed_at timestamptz;
alter table ledger_link add constraint ledger_link_token_is_hash check (token ~ '^[0-9a-f]{64}$') not valid;
alter table ledger_link add constraint ledger_link_lifetime check (expires_at > created_at and expires_at <= created_at + interval '31 days');
alter table ledger_link add constraint ledger_link_creator_fk foreign key (created_by, firm_id) references app_user (id, firm_id);

create function create_share_link(p_revision uuid, p_token_hash text, p_days int, p_label text default null) returns timestamptz
language plpgsql security definer set search_path = public, pg_temp as
$$
declare firm uuid := public.current_firm_id(); until timestamptz;
begin
  if auth.uid() is null or public.current_user_role() not in ('designer', 'approver') then
    raise exception 'only a designer or approver shares a revision' using errcode = '42501';
  end if;
  if p_days is null or p_days < 1 or p_days > 30 then raise exception 'a share link lasts 1 to 30 days'; end if;
  if p_token_hash !~ '^[0-9a-f]{64}$' then raise exception 'bad token hash'; end if;
  if not exists (select 1 from public.revision where id = p_revision and firm_id = firm) then
    raise exception 'revision not found in your firm' using errcode = '42501';
  end if;
  if not exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate3') then
    raise exception 'only a revision signed at Gate 3 can be shared';
  end if;
  until := now() + make_interval(days => p_days);
  insert into public.ledger_link (token, firm_id, revision_id, expires_at, created_by, label)
    values (p_token_hash, firm, p_revision, until, auth.uid(), left(p_label, 80));
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (firm, p_revision, 'share_link_created', jsonb_build_object('by', auth.uid(), 'expires_at', until, 'label', left(p_label, 80)));
  return until;
end $$;

create function revoke_share_link(p_revision uuid, p_token_hash text) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
begin
  if auth.uid() is null or public.current_user_role() not in ('designer', 'approver') then
    raise exception 'only a designer or approver revokes a share link' using errcode = '42501';
  end if;
  update public.ledger_link set revoked_at = now()
    where token = p_token_hash and revision_id = p_revision and firm_id = public.current_firm_id() and revoked_at is null;
  if not found then raise exception 'no such open share link'; end if;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'share_link_revoked', jsonb_build_object('by', auth.uid()));
end $$;

-- Opening a link (the public, read-only door; the API calls it on its service connection). Counts and logs the view.
create function open_share_link(p_token_hash text, p_client text default null) returns table (firm_id uuid, revision_id uuid)
language plpgsql security definer set search_path = public, pg_temp as
$$
declare l public.ledger_link;
begin
  select * into l from public.ledger_link where token = p_token_hash for update;
  if l.token is null or l.revoked_at is not null or l.expires_at <= now() then return; end if;
  update public.ledger_link set views = views + 1, last_viewed_at = now() where token = p_token_hash;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (l.firm_id, l.revision_id, 'share_link_viewed',
            jsonb_build_object('link_created_by', l.created_by, 'expires_at', l.expires_at, 'view', l.views + 1,
                               'client', left(p_client, 120)));
  return query select l.firm_id, l.revision_id;
end $$;

revoke all on function ledger_event_chain(), gate2_context(uuid), audit_row(), signed_revision_immutable(), review_append_only(),
  ledger_row_hash(text, bigint, bigint, uuid, uuid, text, jsonb, timestamptz) from public, anon, authenticated;
revoke all on function gate2_review(uuid, text, text, uuid), gate2_prepare_bulk(uuid), gate2_bulk_approve(uuid),
  sign_gate(uuid, gate, text), create_share_link(uuid, text, int, text), revoke_share_link(uuid, text) from public, anon;
grant execute on function gate2_review(uuid, text, text, uuid), gate2_prepare_bulk(uuid), gate2_bulk_approve(uuid),
  sign_gate(uuid, gate, text), create_share_link(uuid, text, int, text), revoke_share_link(uuid, text) to authenticated;
revoke all on function open_share_link(text, text) from public, anon, authenticated;
