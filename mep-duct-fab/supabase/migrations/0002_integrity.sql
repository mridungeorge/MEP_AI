-- Cross-firm integrity and edition tracking (adversarial review S10, S12).

-- Child rows must share the parent's firm_id: composite FKs on (id, firm_id).
alter table project     add constraint project_id_firm_uq     unique (id, firm_id);
alter table revision    add constraint revision_id_firm_uq    unique (id, firm_id);
alter table system      add constraint system_id_firm_uq      unique (id, firm_id);
alter table rule_result add constraint rule_result_id_firm_uq unique (id, firm_id);
alter table app_user    add constraint app_user_id_firm_uq    unique (id, firm_id);

alter table revision drop constraint revision_project_id_fkey,
  add constraint revision_project_fk foreign key (project_id, firm_id) references project (id, firm_id);
alter table revision drop constraint revision_parent_revision_id_fkey,
  add constraint revision_parent_fk foreign key (parent_revision_id, firm_id) references revision (id, firm_id);
alter table space drop constraint space_revision_id_fkey,
  add constraint space_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table system drop constraint system_revision_id_fkey,
  add constraint system_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table equipment drop constraint equipment_system_id_fkey,
  add constraint equipment_system_fk foreign key (system_id, firm_id) references system (id, firm_id);
alter table rule_result drop constraint rule_result_revision_id_fkey,
  add constraint rule_result_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table artifact drop constraint artifact_revision_id_fkey,
  add constraint artifact_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table review drop constraint review_rule_result_id_fkey,
  add constraint review_rule_result_fk foreign key (rule_result_id, firm_id) references rule_result (id, firm_id);
alter table review drop constraint review_user_id_fkey,
  add constraint review_user_fk foreign key (user_id, firm_id) references app_user (id, firm_id);
alter table signoff drop constraint signoff_revision_id_fkey,
  add constraint signoff_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table signoff drop constraint signoff_user_id_fkey,
  add constraint signoff_user_fk foreign key (user_id, firm_id) references app_user (id, firm_id);
alter table ledger_event drop constraint ledger_event_revision_id_fkey,
  add constraint ledger_event_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);
alter table ledger_link drop constraint ledger_link_revision_id_fkey,
  add constraint ledger_link_revision_fk foreign key (revision_id, firm_id) references revision (id, firm_id);

-- One NCC edition per project (non-negotiable 7): every result records its edition, the edition
-- must match the rule id and the project, and a project's edition is frozen once results exist.
alter table rule_result
  add column edition text not null check (edition in ('NCC2022', 'NCC2025')),
  add constraint rule_result_rule_id_edition check (rule_id like edition || '-%');

create function rule_result_edition_matches_project() returns trigger language plpgsql security definer
  set search_path = public, pg_temp as
$$ begin
  if new.edition is distinct from
     (select p.ncc_edition from public.revision r join public.project p
         on p.id = r.project_id and p.firm_id = r.firm_id
       where r.id = new.revision_id for share of p) then
    raise exception 'rule_result edition % does not match the project NCC edition', new.edition;
  end if;
  return new;
end $$;
create trigger rule_result_edition_check before insert or update on rule_result
  for each row execute function rule_result_edition_matches_project();

create function project_edition_frozen_after_results() returns trigger language plpgsql
  set search_path = public, pg_temp as
$$ begin
  if new.ncc_edition is distinct from old.ncc_edition and exists (
       select 1 from rule_result rr join revision r on r.id = rr.revision_id where r.project_id = old.id) then
    raise exception 'project NCC edition cannot change once rule results exist';
  end if;
  return new;
end $$;
create trigger project_edition_frozen before update on project
  for each row execute function project_edition_frozen_after_results();
