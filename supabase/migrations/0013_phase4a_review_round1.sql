-- Phase 4a adversarial review, round 1 (0010 is not edited).

-- S3. The backfill bypass is gone: the ledger is append-only for everybody, the owner included (short of disabling the trigger, which is DDL).
create or replace function ledger_event_immutable() returns trigger language plpgsql as
$$ begin raise exception 'ledger_event is append-only'; end $$;

-- S4. A head row per firm, advanced only by the chain trigger. verify_ledger checks the last ledger row against it, so deleting the END
--     of the chain is detected too (not only rows before a sign-off anchor). An attacker who can also edit this table can still hide it:
--     the head (seq, hash) is printed on every signed package so that it can be kept outside the database.
create table ledger_head (
  firm_id uuid primary key references firm(id),
  seq bigint not null,
  row_hash text not null check (row_hash ~ '^[0-9a-f]{64}$')
);
insert into ledger_head (firm_id, seq, row_hash)
  select distinct on (firm_id) firm_id, seq, row_hash from ledger_event order by firm_id, seq desc;
alter table ledger_head enable row level security;
create policy ledger_head_firm_select on ledger_head for select using (firm_id = current_firm_id());
revoke all on ledger_head from anon;
revoke insert, update, delete, truncate, trigger, references on ledger_head from authenticated;

create or replace function ledger_event_chain() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare last_seq bigint; last_hash text;
begin
  perform pg_advisory_xact_lock(hashtextextended('ledger:' || new.firm_id::text, 0));
  select seq, row_hash into last_seq, last_hash from public.ledger_event where firm_id = new.firm_id order by seq desc limit 1;
  new.seq := coalesce(last_seq, 0) + 1;
  new.prev_hash := coalesce(last_hash, repeat('0', 64));
  new.row_hash := public.ledger_row_hash(new.prev_hash, new.seq, new.id, new.firm_id, new.revision_id, new.kind, new.payload,
                                         new.created_at);
  insert into public.ledger_head (firm_id, seq, row_hash) values (new.firm_id, new.seq, new.row_hash)
    on conflict (firm_id) do update set seq = excluded.seq, row_hash = excluded.row_hash;
  return new;
end $$;

create or replace function verify_ledger(p_firm uuid)
returns table (ok boolean, checked bigint, broken_seq bigint, reason text, head_seq bigint, head_hash text)
language plpgsql stable security definer set search_path = public, pg_temp as
$$
declare r record; prev text := repeat('0', 64); expected bigint := 1; n bigint := 0; s record; anchor text; h public.ledger_head;
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
  select * into h from public.ledger_head where firm_id = p_firm;
  if (h.firm_id is null and n > 0) or (h.firm_id is not null and (h.seq is distinct from n or h.row_hash is distinct from prev)) then
    return query select false, n, n, 'the end of the ledger does not match its recorded head (rows removed from the end?)',
                        null::bigint, null::text;
    return;
  end if;
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

-- S7. The reasons a result got its class are stored with it (the classifier knows the parent revision; a later recomputation does not).
alter table rule_result add column review_reasons jsonb;
create or replace function rule_result_frozen_guard() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare rev uuid; locked boolean;
begin
  for rev in select distinct x from unnest(array[old.revision_id, new.revision_id]) x where x is not null loop
    select frozen_at is not null into locked from public.revision where id = rev for share;
    if locked then
      if tg_op = 'UPDATE' and old.revision_id = new.revision_id
         and (to_jsonb(new) - 'review_class' - 'review_reasons') = (to_jsonb(old) - 'review_class' - 'review_reasons') then
        continue;
      end if;
      raise exception 'revision is frozen: its results cannot be replaced or removed';
    end if;
  end loop;
  return coalesce(new, old);
end $$;

-- S2. Reviews, bulk approvals and the Gate 2 signature serialise on the revision row (a review cannot slip in beside the signature).
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
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
    raise exception 'Gate 2 is already signed';
  end if;
  return firm;
end $$;

-- B1 + S1. ONE problem found in a spot-check ends bulk approval for the revision for good (it cannot be undone by re-approving the
--          line), and a sample cannot be redrawn while one is open.
create or replace function gate2_prepare_bulk(p_revision uuid) returns uuid language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare firm uuid; cand uuid[]; smp uuid[]; n int; k int; sid uuid;
begin
  firm := public.gate2_context(p_revision);
  if exists (select 1 from public.review where revision_id = p_revision and spot_check and decision <> 'approve') then
    raise exception 'a spot-check found a problem: every clean pass must be reviewed line by line';
  end if;
  if exists (select 1 from public.review_sample where revision_id = p_revision and used_at is null) then
    raise exception 'a spot-check sample is already open: examine it and approve, or review line by line';
  end if;
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

create or replace function gate2_bulk_approve(p_sample uuid) returns int language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare smp public.review_sample; firm uuid; now_cand uuid[]; missing int; bad int; done int;
begin
  select * into smp from public.review_sample where id = p_sample and firm_id = public.current_firm_id() and created_by = auth.uid();
  if smp.id is null then raise exception 'no such spot-check sample of yours'; end if;
  firm := public.gate2_context(smp.revision_id);
  if smp.used_at is not null then raise exception 'this sample was already used'; end if;
  select count(*) into bad from public.review where revision_id = smp.revision_id and spot_check and decision <> 'approve';
  if bad > 0 then
    raise exception 'the spot-check found % problem(s): review every clean pass line by line', bad;
  end if;
  select coalesce(array_agg(rr.id order by rr.id), '{}') into now_cand from public.rule_result rr
    where rr.revision_id = smp.revision_id and rr.firm_id = firm and rr.current and rr.review_class = 'clean_pass' and not rr.stale
      and (not exists (select 1 from public.review v where v.rule_result_id = rr.id) or rr.id = any (smp.sample_ids));
  if not (smp.candidate_ids <@ now_cand and now_cand <@ smp.candidate_ids) then
    raise exception 'the set of clean passes changed since the sample was drawn: draw a new sample';
  end if;
  select count(*) into missing from unnest(smp.sample_ids) s(id)
    where not exists (select 1 from public.review v where v.rule_result_id = s.id and v.sample_id = smp.id and v.user_id = auth.uid());
  if missing > 0 then raise exception '% sampled result(s) have not been examined yet', missing; end if;
  insert into public.review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason, bulk, sample_id)
    select firm, c.id, smp.revision_id, 'gate2', auth.uid(), 'approve',
           'bulk approval of a clean pass after a random spot-check of ' || cardinality(smp.sample_ids) || ' of '
             || cardinality(smp.candidate_ids), true, smp.id
      from unnest(smp.candidate_ids) c(id)
     where not exists (select 1 from public.review v where v.rule_result_id = c.id);
  get diagnostics done = row_count;
  update public.review_sample set used_at = now() where id = smp.id;
  return done;
end $$;

-- S5. A person signs at most ONE gate of a revision (a role change between gates cannot make one person two signers).
create or replace function sign_gate(p_revision uuid, p_gate gate, p_registration text default null) returns bigint
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
  if exists (select 1 from public.signoff where revision_id = p_revision and user_id = me.id) then
    raise exception 'you already signed another gate of this revision: each gate needs a different person';
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
