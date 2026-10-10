-- Phase 4b: PDF drawings may be uploaded (rendered and read by a vision model into the evidence table only). The storage policy of 0009 takes
-- one more extension; everything else about it (own firm, own open revision, designer only, hash-named file) is unchanged.
drop policy uploads_designer_insert on storage.objects;
create policy uploads_designer_insert on storage.objects for insert to authenticated
  with check (
    bucket_id = 'uploads'
    and array_length(string_to_array(name, '/'), 1) = 3
    and (string_to_array(name, '/'))[1] = public.current_firm_id()::text
    and public.current_user_role() = 'designer'
    and exists (select 1 from public.revision r
                 where r.id::text = (string_to_array(name, '/'))[2] and r.firm_id = public.current_firm_id()
                   and r.frozen_at is null)
    and (string_to_array(name, '/'))[3] ~ '^[0-9a-f]{64}\.(ifc|dxf|pdf)$');
