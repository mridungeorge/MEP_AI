-- Phase 4a.1 adversarial review, round 2 (0015 and 0016 are not edited).

-- Two approvers can each acknowledge the same accepted FAIL; Gate 3 counts only the SIGNER's own. (With one row per result, an approver
-- who acknowledged and then left would have made the revision unsignable for everybody else.)
alter table fail_ack drop constraint fail_ack_rule_result_id_key;
alter table fail_ack add constraint fail_ack_rule_result_user_key unique (rule_result_id, user_id);

-- The DEMO- placeholder check ignores case (the registration number comparison does too).
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
        and not exists (select 1 from public.fail_ack a where a.rule_result_id = v.rule_result_id and a.user_id = me.id)
        and exists (select 1 from public.rule_result rr where rr.id = v.rule_result_id and rr.current);
    if ack_n > 0 then
      raise exception '% accepted FAIL(s) need the approver''s individual acknowledgement before Gate 3', ack_n;
    end if;
    if me.registration_no is null or length(btrim(me.registration_no)) < 3 then
      raise exception 'the approver has no registration number on file';
    end if;
    if upper(me.registration_no) like 'DEMO-%' and not exists (select 1 from public.firm f where f.id = firm and f.name like 'Demo Mechanical (synthetic)%') then
      raise exception 'DEMO- registration numbers are placeholders and are not valid outside the demo firm';
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
