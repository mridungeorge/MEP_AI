-- Phase 4a adversarial review, round 2 (0010 and 0013 are not edited).
-- Honest note on 0013's ledger_head: it detects deletion of the END of the chain by anyone who cannot also rewrite ledger_head. It is NOT
-- proof against the database owner, who can disable triggers and recompute unkeyed hashes. The signed package prints the ledger
-- position fixed at the last sign-off (not the live head, which would change the PDF every time it is opened). Keeping the head outside
-- the database is an operational control, recorded in docs/STATUS.md.

-- B1. A result in an open spot-check sample can only be decided AS PART of that sample, and ANY non-approve decision on a sampled or
--     candidate result (whatever its spot_check flag) ends bulk approval for good.
create or replace function gate2_review(p_result uuid, p_decision text, p_reason text, p_sample uuid default null) returns bigint
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
  elsif exists (select 1 from public.review_sample where revision_id = rev and used_at is null and p_result = any (sample_ids)) then
    raise exception 'this result is in an open spot-check sample: decide it as part of the sample';
  end if;
  insert into public.review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason, spot_check, sample_id)
    values (firm, p_result, rev, 'gate2', auth.uid(), p_decision, btrim(p_reason), p_sample is not null, p_sample)
    returning seq into seq_out;
  return seq_out;
end $$;

-- S5. The person who signed Gate 1 (froze the revision) does not review it; a Gate 2 signer must have reviewed something; an approver
--     must not have reviewed anything; and a person signs at most one gate.
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
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate1' and user_id = auth.uid()) then
    raise exception 'you signed Gate 1 of this revision: someone else must review it';
  end if;
  if exists (select 1 from public.signoff where revision_id = p_revision and gate = 'gate2') then
    raise exception 'Gate 2 is already signed';
  end if;
  return firm;
end $$;

-- helper: the unreviewed clean passes right now (plus those already decided as part of a given sample)
create function gate2_clean_set(p_revision uuid, p_firm uuid, p_sample uuid[]) returns uuid[] language sql stable security definer
  set search_path = public, pg_temp as
$$
  select coalesce(array_agg(rr.id order by rr.id), '{}') from public.rule_result rr
   where rr.revision_id = p_revision and rr.firm_id = p_firm and rr.current and rr.review_class = 'clean_pass' and not rr.stale
     and (not exists (select 1 from public.review v where v.rule_result_id = rr.id) or rr.id = any (p_sample));
$$;
revoke all on function gate2_clean_set(uuid, uuid, uuid[]) from public, anon, authenticated;

create or replace function gate2_prepare_bulk(p_revision uuid) returns uuid language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare firm uuid; cand uuid[]; smp uuid[]; n int; k int; sid uuid; open_s public.review_sample;
begin
  firm := public.gate2_context(p_revision);
  if exists (select 1 from public.review where revision_id = p_revision and decision <> 'approve'
               and (spot_check or sample_id is not null)) then
    raise exception 'a spot-check found a problem: every clean pass must be reviewed line by line';
  end if;
  select * into open_s from public.review_sample where revision_id = p_revision and used_at is null;
  if open_s.id is not null then
    if public.gate2_clean_set(p_revision, firm, open_s.sample_ids) <@ open_s.candidate_ids
       and open_s.candidate_ids <@ public.gate2_clean_set(p_revision, firm, open_s.sample_ids) then
      raise exception 'a spot-check sample is already open: examine it and approve, or review line by line';
    end if;
    update public.review_sample set used_at = now() where id = open_s.id;     -- the clean set changed under it: it can never be used
  end if;
  if exists (select 1 from public.rule_result where revision_id = p_revision and firm_id = firm and current and review_class is null) then
    raise exception 'results have not been classified';
  end if;
  cand := public.gate2_clean_set(p_revision, firm, '{}');
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
declare smp public.review_sample; firm uuid; missing int; bad int; done int;
begin
  select * into smp from public.review_sample where id = p_sample and firm_id = public.current_firm_id() and created_by = auth.uid();
  if smp.id is null then raise exception 'no such spot-check sample of yours'; end if;
  firm := public.gate2_context(smp.revision_id);
  if smp.used_at is not null then raise exception 'this sample was already used'; end if;
  select count(*) into bad from public.review v
   where v.revision_id = smp.revision_id and v.decision <> 'approve'
     and (v.spot_check or v.sample_id is not null or v.rule_result_id = any (smp.candidate_ids));
  if bad > 0 then
    raise exception 'the spot-check found % problem(s): review every clean pass line by line', bad;
  end if;
  if not (smp.candidate_ids <@ public.gate2_clean_set(smp.revision_id, firm, smp.sample_ids)
          and public.gate2_clean_set(smp.revision_id, firm, smp.sample_ids) <@ smp.candidate_ids) then
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

-- S5 + #3. Signing: separation of duties, and a bulk approval only stands while the result is still a clean pass.
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
    if exists (select 1 from public.review where revision_id = p_revision and user_id = me.id) then
      raise exception 'you reviewed this revision at Gate 2: the approver must be someone else';
    end if;
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
