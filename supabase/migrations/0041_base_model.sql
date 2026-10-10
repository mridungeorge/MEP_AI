-- Phase 8.3/8.6: the architect's IFC kept for a revision, so a drafting job can be given it (ifc-mep) and the browser can show it (3D preview).
-- Content is readable only through a function that checks the firm; the table's content column is not granted to clients.
create table base_model (
  id uuid primary key default gen_random_uuid(),
  firm_id uuid not null references firm(id),
  revision_id uuid not null,
  file_name text not null check (length(file_name) between 1 and 200),
  file_sha256 text not null check (file_sha256 ~ '^[0-9a-f]{64}$'),
  schema_name text not null check (length(schema_name) <= 20),
  storeys jsonb not null default '[]',
  content bytea not null check (octet_length(content) between 1 and 104857600),
  created_by uuid not null,
  created_at timestamptz not null default now(),
  constraint base_model_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id),
  constraint base_model_user_fk foreign key (created_by, firm_id) references app_user (id, firm_id),
  unique (revision_id, file_sha256)
);
alter table base_model enable row level security;
create policy base_model_select on base_model for select using (firm_id = current_firm_id());
revoke all on base_model from anon;
revoke select on base_model from authenticated;
grant select (id, firm_id, revision_id, file_name, file_sha256, schema_name, storeys, created_by, created_at) on base_model to authenticated;
revoke insert, update, delete, truncate, trigger, references on base_model from authenticated;
create trigger base_model_frozen before insert on base_model for each row execute function reject_if_revision_frozen();
create trigger base_model_append_only before update or delete on base_model for each row execute function review_append_only();

create function base_model_file(p_id uuid) returns table (file_name text, content bytea)
language plpgsql security definer set search_path = public, pg_temp as
$$
begin
  if auth.uid() is null or public.current_firm_id() is null then raise exception 'not signed in' using errcode = '42501'; end if;
  return query select b.file_name, b.content from public.base_model b where b.id = p_id and b.firm_id = public.current_firm_id();
end $$;
revoke all on function base_model_file(uuid) from public, anon;
grant execute on function base_model_file(uuid) to authenticated;
