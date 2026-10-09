-- Sprint 2.5: uploaded IFC and DXF files live in Supabase Storage, in a private bucket, one folder per firm:
--   uploads/<firm_id>/<revision_id>/<sha256>.<ifc|dxf>
-- A designer may add a file under their own firm and one of the firm's revisions; any role of the firm may read the firm's
-- files. Nobody (but the service role) may change or delete a stored file: it is the evidence the extraction came from.
-- The file TYPE is checked by the API from the file's content, not here (storage only sees names).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
  values ('uploads', 'uploads', false, 52428800, null)
  on conflict (id) do update set public = false, file_size_limit = excluded.file_size_limit;

create policy uploads_firm_select on storage.objects for select to authenticated
  using (bucket_id = 'uploads' and (string_to_array(name, '/'))[1] = public.current_firm_id()::text);

create policy uploads_designer_insert on storage.objects for insert to authenticated
  with check (
    bucket_id = 'uploads'
    and array_length(string_to_array(name, '/'), 1) = 3
    and (string_to_array(name, '/'))[1] = public.current_firm_id()::text
    and public.current_user_role() = 'designer'
    and exists (select 1 from public.revision r
                 where r.id::text = (string_to_array(name, '/'))[2] and r.firm_id = public.current_firm_id())
    and (string_to_array(name, '/'))[3] ~ '^[0-9a-f]{64}\.(ifc|dxf)$');
