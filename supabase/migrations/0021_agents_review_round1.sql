-- Phase 4b review round 1 (0019 is not edited): who may close an agent note, bound to the revision it belongs to.
drop function agent_note_resolve(uuid, text);
create function agent_note_resolve(p_revision uuid, p_note uuid, p_status text) returns void language plpgsql security definer
  set search_path = public, pg_temp as
$$
declare k text;
begin
  if auth.uid() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  if p_status not in ('answered', 'dismissed') then raise exception 'unknown status %', p_status; end if;
  select kind into k from public.agent_note where id = p_note and revision_id = p_revision and firm_id = public.current_firm_id() and status = 'open';
  if k is null then raise exception 'no such open note in this revision of your firm' using errcode = '42501'; end if;
  -- a flag or a risk is the reviewers' to close: the designer whose work it questions cannot dismiss it
  if k in ('flag', 'risk') and public.current_user_role() not in ('checker', 'approver') then
    raise exception 'only a checker or approver closes a flag or a risk' using errcode = '42501';
  end if;
  update public.agent_note set status = p_status, resolved_by = auth.uid(), resolved_at = now() where id = p_note;
  insert into public.ledger_event (firm_id, revision_id, kind, payload)
    values (public.current_firm_id(), p_revision, 'agent_note_' || p_status, jsonb_build_object('note', p_note, 'by', auth.uid(), 'kind', k));
end $$;
revoke all on function agent_note_resolve(uuid, uuid, text) from public, anon;
grant execute on function agent_note_resolve(uuid, uuid, text) to authenticated;
