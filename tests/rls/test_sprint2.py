"""Sprint 2 tables: building_part, system_input, extraction, and the Gate 1 confirmation guard (attacks per role/firm)."""
import uuid

import psycopg
import pytest

from tests.rls.conftest import as_user, uid

BLOCKED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation, psycopg.errors.RaiseException,
           psycopg.errors.ForeignKeyViolation, psycopg.errors.NotNullViolation, psycopg.errors.UniqueViolation)
SHA = "a" * 64


@pytest.fixture
def fresh(admin, world):
    """A new project and open revision for firm A with no results yet (parts are editable), plus one system."""
    a = world.firms["A"]
    project, rev, system = uid(), uid(), uid()
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, building_class, ncc_edition)"
                  " values (%s, %s, '2 Test St', 'VIC', 6, '5', 'NCC2025')", (project, a.id))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                  (rev, a.id, project))
    admin.execute("insert into system (id, firm_id, revision_id, type) values (%s, %s, %s, 'air_conditioning')",
                  (system, a.id, rev))
    return {"project": project, "revision": rev, "system": system, "firm": a.id}


def new_run(admin, fresh):
    rid = uid()
    admin.execute("insert into ingest_run (id, firm_id, revision_id, source_kind, source_name, source_sha256)"
                  " values (%s, %s, %s, 'pdf', 'plan.pdf', %s)", (rid, fresh["firm"], fresh["revision"], uid().replace("-", "") * 2))
    return rid


def attempt(user, sql, params=()):
    """Run one statement as a client; returns (ok, rowcount)."""
    with as_user(user) as cur:
        try:
            cur.execute(sql, params)
        except BLOCKED:
            return False, 0
        return True, cur.rowcount


# ---- building_part ------------------------------------------------------------------------------

PART = ("insert into building_part (firm_id, project_id, position, building_class, storeys, area_m2_value,"
        " area_m2_provenance) values (%s, %s, %s, %s, 3, 500, 'default')")


def test_a_designer_manages_parts_of_their_own_open_project(world, fresh):
    user = world.firms["A"].users["designer"]
    assert attempt(user, PART, (fresh["firm"], fresh["project"], 0, "5")) == (True, 1)
    assert attempt(user, PART, (fresh["firm"], fresh["project"], 1, "2")) == (True, 1)


@pytest.mark.parametrize("cls,storeys,area", [("99", 3, 500), ("5", 0, 500), ("5", 201, 500), ("5", 3, 0), ("5", 3, -5),
                                              ("5", 3, 1e9)])
def test_parts_have_a_closed_class_domain_and_sane_numbers(world, fresh, cls, storeys, area):
    user = world.firms["A"].users["designer"]
    sql = ("insert into building_part (firm_id, project_id, position, building_class, storeys, area_m2_value,"
           " area_m2_provenance) values (%s, %s, 0, %s, %s, %s, 'default')")
    assert attempt(user, sql, (fresh["firm"], fresh["project"], cls, storeys, area))[0] is False


def test_an_area_needs_a_provenance_and_a_unit_that_is_square_metres(world, fresh):
    user = world.firms["A"].users["designer"]
    assert not attempt(user, "insert into building_part (firm_id, project_id, position, building_class,"
                             " area_m2_value) values (%s, %s, 0, '5', 100)", (fresh["firm"], fresh["project"]))[0]
    assert not attempt(user, "insert into building_part (firm_id, project_id, position, building_class,"
                             " area_m2_value, area_m2_unit, area_m2_provenance) values (%s, %s, 0, '5', 100, 'ft^2',"
                             " 'engineer_confirmed')", (fresh["firm"], fresh["project"]))[0]


def test_another_firm_cannot_read_or_write_parts(admin, world, fresh):
    admin.execute(PART, (fresh["firm"], fresh["project"], 0, "5"))
    intruder = world.firms["B"].users["designer"]
    with as_user(intruder) as cur:
        cur.execute("select count(*) from building_part where project_id = %s", (fresh["project"],))
        assert cur.fetchone()[0] == 0
    b = world.firms["B"].id
    assert not attempt(intruder, PART, (b, fresh["project"], 5, "5"))[0]        # composite FK: project is firm A's
    assert not attempt(intruder, PART, (fresh["firm"], fresh["project"], 6, "5"))[0]
    assert attempt(intruder, "delete from building_part where project_id = %s", (fresh["project"],)) == (True, 0)
    assert attempt(intruder, "update building_part set building_class = '2' where project_id = %s",
                   (fresh["project"],)) == (True, 0)


def test_parts_cannot_be_moved_to_another_project_or_firm(admin, world, fresh):
    admin.execute(PART, (fresh["firm"], fresh["project"], 0, "5"))
    user = world.firms["A"].users["designer"]
    assert not attempt(user, "update building_part set project_id = %s where project_id = %s",
                       (world.firms["A"].project, fresh["project"]))[0]
    assert not attempt(user, "update building_part set firm_id = %s where project_id = %s",
                       (world.firms["B"].id, fresh["project"]))[0]


def test_parts_freeze_once_results_exist_or_a_revision_is_frozen_for_every_role(admin, world, fresh):
    admin.execute(PART, (fresh["firm"], fresh["project"], 0, "5"))
    admin.execute("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
                  " values (%s, %s, 'NCC2025-J6D3-econ-cycle', 'NCC2025', 'PASS', '{}', '{}')",
                  (fresh["firm"], fresh["revision"]))
    user = world.firms["A"].users["designer"]
    assert not attempt(user, "update building_part set building_class = '2' where project_id = %s", (fresh["project"],))[0]
    assert not attempt(user, "delete from building_part where project_id = %s", (fresh["project"],))[0]
    assert not attempt(user, PART, (fresh["firm"], fresh["project"], 1, "2"))[0]
    with pytest.raises(BLOCKED):
        admin.execute("update building_part set building_class = '2' where project_id = %s", (fresh["project"],))


# ---- extraction ----------------------------------------------------------------------------------

INSERT_EXTRACTION = (
    "insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name, source_sha256, entity_kind,"
    " entity_key, field, value_number, unit) values (%s, %s, %s, %s, 'plan.pdf', %s, 'space', 'S1', 'area_m2', 41.5,"
    " 'm^2')")


def test_the_service_role_writes_extraction_rows_clients_only_read_them(admin, world, fresh):
    admin.execute(INSERT_EXTRACTION, (fresh["firm"], fresh["revision"], new_run(admin, fresh), "pdf", SHA))
    for role in ("designer", "checker", "approver"):
        user = world.firms["A"].users[role]
        with as_user(user) as cur:
            cur.execute("select provenance::text from extraction where revision_id = %s", (fresh["revision"],))
            assert [r[0] for r in cur.fetchall()] == ["extracted"]
        assert not attempt(user, INSERT_EXTRACTION, (fresh["firm"], fresh["revision"], new_run(admin, fresh), "pdf", SHA))[0]
        ok, changed = attempt(user, "update extraction set value_number = 1 where revision_id = %s", (fresh["revision"],))
        assert not ok or changed == 0
        assert not attempt(user, "delete from extraction where revision_id = %s", (fresh["revision"],))[0]
    with as_user(world.firms["B"].users["designer"]) as cur:
        cur.execute("select count(*) from extraction where revision_id = %s", (fresh["revision"],))
        assert cur.fetchone()[0] == 0


def test_extraction_is_always_provenance_extracted_and_append_only(admin, fresh):
    with pytest.raises(BLOCKED):
        admin.execute("insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name,"
                      " source_sha256, entity_kind, entity_key, field, value_text, provenance) values"
                      " (%s, %s, %s, 'pdf', 'p.pdf', %s, 'space', 'S', 'name', 'Office', 'engineer_confirmed')",
                      (fresh["firm"], fresh["revision"], new_run(admin, fresh), SHA))
    admin.execute(INSERT_EXTRACTION, (fresh["firm"], fresh["revision"], new_run(admin, fresh), "pdf", SHA))
    with pytest.raises(BLOCKED):
        admin.execute("update extraction set provenance = 'engineer_confirmed' where revision_id = %s", (fresh["revision"],))
    with pytest.raises(BLOCKED):
        admin.execute("delete from extraction where revision_id = %s", (fresh["revision"],))


@pytest.mark.parametrize("sql", [
    ("insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name, source_sha256, entity_kind,"
    " entity_key, field, value_number) values (%s, %s, %s, 'pdf', 'p', %s, 'space', 'S', 'area_m2', 5)"),  # no unit
    ("insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name, source_sha256, entity_kind,"
    " entity_key, field) values (%s, %s, %s, 'pdf', 'p', %s, 'space', 'S', 'name')"),                      # no value
    ("insert into extraction (firm_id, revision_id, ingest_run, source_kind, source_name, source_sha256, entity_kind,"
    " entity_key, field, value_text) values (%s, %s, %s, 'dwg', 'p', %s, 'space', 'S', 'name', 'x')"),     # no DWG
])
def test_extraction_needs_a_unit_for_numbers_one_value_and_a_known_source(admin, fresh, sql):
    with pytest.raises(BLOCKED):
        admin.execute(sql, (fresh["firm"], fresh["revision"], new_run(admin, fresh), SHA))


def test_extraction_cannot_be_added_to_a_frozen_revision(admin, fresh):
    run = new_run(admin, fresh)
    admin.execute("update revision set frozen_at = now() where id = %s", (fresh["revision"],))
    with pytest.raises(BLOCKED):
        admin.execute(INSERT_EXTRACTION, (fresh["firm"], fresh["revision"], run, "pdf", SHA))
    with pytest.raises(BLOCKED):
        new_run(admin, fresh)


def test_an_ingest_run_is_unique_per_file_and_revision_and_append_only(admin, fresh):
    sha = "b" * 64
    sql = ("insert into ingest_run (firm_id, revision_id, source_kind, source_name, source_sha256, health)"
           " values (%s, %s, 'ifc', 'm.ifc', %s, '{\"score_percent\": 90}')")
    admin.execute(sql, (fresh["firm"], fresh["revision"], sha))
    with pytest.raises(BLOCKED):
        admin.execute(sql, (fresh["firm"], fresh["revision"], sha))
    with pytest.raises(BLOCKED):
        admin.execute("update ingest_run set health = '{}' where revision_id = %s", (fresh["revision"],))
    with pytest.raises(BLOCKED):
        admin.execute("delete from ingest_run where revision_id = %s", (fresh["revision"],))


def test_clients_can_read_their_ingest_runs_but_not_write_them(admin, world, fresh):
    new_run(admin, fresh)
    with as_user(world.firms["A"].users["designer"]) as cur:
        cur.execute("select count(*) from ingest_run where revision_id = %s", (fresh["revision"],))
        assert cur.fetchone()[0] == 1
    with as_user(world.firms["B"].users["designer"]) as cur:
        cur.execute("select count(*) from ingest_run where revision_id = %s", (fresh["revision"],))
        assert cur.fetchone()[0] == 0
    sql = ("insert into ingest_run (firm_id, revision_id, source_kind, source_name, source_sha256)"
           " values (%s, %s, 'pdf', 'x.pdf', %s)")
    assert not attempt(world.firms["A"].users["designer"], sql, (fresh["firm"], fresh["revision"], "c" * 64))[0]


# ---- system_input and Gate 1 -----------------------------------------------------------------------

INPUT = ("insert into system_input (id, firm_id, system_id, name, value_number, unit, provenance, source)"
         " values (%s, %s, %s, 'control_deadband', 3, %s, %s, 'xlsx')")


def add_input(admin, fresh, unit="K", provenance="extracted"):
    iid = uid()
    admin.execute(INPUT, (iid, fresh["firm"], fresh["system"], unit, provenance))
    return iid


def test_a_numeric_input_needs_a_unit_one_value_and_a_clean_name(admin, fresh):
    for sql in (
        "insert into system_input (firm_id, system_id, name, value_number, provenance) values (%s, %s, 'x', 1, 'default')",
        "insert into system_input (firm_id, system_id, name, provenance) values (%s, %s, 'x', 'default')",
        ("insert into system_input (firm_id, system_id, name, value_number, value_text, unit, provenance)"
        " values (%s, %s, 'x', 1, 'a', 'K', 'default')"),
        "insert into system_input (firm_id, system_id, name, value_text, provenance) values (%s, %s, 'Bad Name', 'a', 'default')",
    ):
        with pytest.raises(BLOCKED):
            admin.execute(sql, (fresh["firm"], fresh["system"]))


CONFIRM = "select gate1_confirm('system_input', array[%s]::uuid[])"


def confirmed_state(admin, iid):
    return admin.execute("select provenance::text, confirmed_by is not null from system_input where id = %s",
                         (iid,)).fetchone()


def test_a_designer_confirms_through_gate1_confirm_and_the_clock_is_the_servers(admin, world, fresh):
    iid = add_input(admin, fresh)
    designer = world.firms["A"].users["designer"]
    with as_user(designer) as cur:
        cur.execute(CONFIRM, (iid,))
        assert cur.fetchone()[0] == 1
        cur.execute("select provenance::text, confirmed_by, confirmed_at > now() - interval '1 minute'"
                    " from system_input where id = %s", (iid,))
        assert cur.fetchone() == ("engineer_confirmed", uuid.UUID(designer), True)


@pytest.mark.parametrize("role", ["checker", "approver"])
def test_only_a_designer_confirms(admin, world, fresh, role):
    iid = add_input(admin, fresh)
    assert not attempt(world.firms["A"].users[role], CONFIRM, (iid,))[0]
    assert confirmed_state(admin, iid) == ("extracted", False)


def test_another_firm_cannot_confirm_my_rows_and_unknown_ids_fail_the_whole_call(admin, world, fresh):
    iid = add_input(admin, fresh)
    assert not attempt(world.firms["B"].users["designer"], CONFIRM, (iid,))[0]
    designer = world.firms["A"].users["designer"]
    assert not attempt(designer, "select gate1_confirm('system_input', array[%s, %s]::uuid[])", (iid, uid()))[0]
    assert not attempt(designer, "select gate1_confirm('system', array[%s]::uuid[])", (iid,))[0]
    assert not attempt(designer, "select gate1_confirm('system_input', '{}'::uuid[])")[0]
    assert confirmed_state(admin, iid) == ("extracted", False)


def test_a_client_cannot_write_provenance_or_the_confirmation_itself(admin, world, fresh):
    iid = add_input(admin, fresh)
    designer = world.firms["A"].users["designer"]
    for sql, params in (
            ("update system_input set provenance = 'engineer_confirmed' where id = %s", (iid,)),
            ("update system_input set confirmed_by = %s, confirmed_at = now() where id = %s", (designer, iid)),
            ("update system_input set provenance = 'default', confirmed_by = %s where id = %s", (designer, iid))):
        assert not attempt(designer, sql, params)[0], sql
    ins = ("insert into system_input (firm_id, system_id, name, value_number, unit, provenance, confirmed_by,"
           " confirmed_at) values (%s, %s, 'x_one', 1, 'K', %s, %s, now())")
    assert not attempt(designer, ins, (fresh["firm"], fresh["system"], "engineer_confirmed", designer))[0]
    assert not attempt(designer, ins, (fresh["firm"], fresh["system"], "default", designer))[0]
    assert confirmed_state(admin, iid) == ("extracted", False)


def test_a_client_can_enter_a_value_as_default_and_it_is_unconfirmed(admin, world, fresh):
    designer = world.firms["A"].users["designer"]
    sql = ("insert into system_input (firm_id, system_id, name, value_number, unit, provenance, source)"
           " values (%s, %s, 'control_deadband', 3, 'K', 'default', 'form')")
    assert attempt(designer, sql, (fresh["firm"], fresh["system"])) == (True, 1)


def test_editing_a_confirmed_value_withdraws_the_confirmation_and_must_reset_provenance(admin, world, fresh):
    iid = add_input(admin, fresh)
    designer = world.firms["A"].users["designer"]
    admin.execute("update system_input set provenance = 'engineer_confirmed', confirmed_by = %s, confirmed_at = now()"
                  " where id = %s", (designer, iid))
    assert not attempt(designer, "update system_input set value_number = 1 where id = %s", (iid,))[0]
    with as_user(designer) as cur:
        cur.execute("update system_input set value_number = 1, provenance = 'default' where id = %s"
                    " returning confirmed_by, confirmed_at, provenance::text", (iid,))
        assert cur.fetchone() == (None, None, "default")


def test_other_firms_cannot_see_inputs(admin, world, fresh):
    iid = add_input(admin, fresh)
    with as_user(world.firms["B"].users["designer"]) as cur:
        cur.execute("select count(*) from system_input where id = %s", (iid,))
        assert cur.fetchone()[0] == 0


def test_a_frozen_revision_blocks_schedule_input_changes_for_every_role(admin, world, fresh):
    iid = add_input(admin, fresh)
    admin.execute("update revision set frozen_at = now() where id = %s", (fresh["revision"],))
    user = world.firms["A"].users["designer"]
    assert not attempt(user, "update system_input set value_number = 5, provenance = 'default' where id = %s", (iid,))[0]
    assert not attempt(user, "delete from system_input where id = %s", (iid,))[0]
    assert not attempt(user, CONFIRM, (iid,))[0]
    with pytest.raises(BLOCKED):
        admin.execute("delete from system_input where id = %s", (iid,))


def test_space_confirmation_follows_the_same_rules(admin, world, fresh):
    sid = uid()
    admin.execute("insert into space (id, firm_id, revision_id, ifc_guid, name, storey, area_m2_value,"
                  " area_m2_provenance) values (%s, %s, %s, 'g1', 'Office', 'L1', 40, 'extracted')",
                  (sid, fresh["firm"], fresh["revision"]))
    designer = world.firms["A"].users["designer"]
    checker = world.firms["A"].users["checker"]
    call = "select gate1_confirm('space', array[%s]::uuid[])"
    assert not attempt(checker, call, (sid,))[0]
    assert not attempt(designer, "update space set confirmed_by = %s, confirmed_at = now() where id = %s", (designer, sid))[0]
    with as_user(designer) as cur:
        cur.execute(call, (sid,))
        cur.execute("select area_m2_provenance::text, ceiling_void_mm_provenance::text, confirmed_by from space"
                    " where id = %s", (sid,))
        assert cur.fetchone() == ("engineer_confirmed", None, uuid.UUID(designer))
    admin.execute("update space set confirmed_by = %s, confirmed_at = now(), area_m2_provenance = 'engineer_confirmed'"
                  " where id = %s", (designer, sid))
    with as_user(designer) as cur:
        cur.execute("update space set area_m2_value = 55, area_m2_provenance = 'default' where id = %s"
                    " returning confirmed_by", (sid,))
        assert cur.fetchone() == (None,)


# ---- review round: parts confirmation, designer-only writes, service-role edits withdraw confirmation ---------

@pytest.mark.parametrize("role", ["checker", "approver"])
def test_only_a_designer_writes_parts_and_schedule_inputs(admin, world, fresh, role):
    user = world.firms["A"].users[role]
    assert not attempt(user, PART, (fresh["firm"], fresh["project"], 0, "5"))[0]
    sql = ("insert into system_input (firm_id, system_id, name, value_number, unit, provenance, source)"
           " values (%s, %s, 'control_deadband', 3, 'K', 'default', 'form')")
    assert not attempt(user, sql, (fresh["firm"], fresh["system"]))[0]
    admin.execute(PART, (fresh["firm"], fresh["project"], 0, "5"))
    assert attempt(user, "update building_part set building_class = '9a' where project_id = %s", (fresh["project"],)) == (True, 0)
    assert attempt(user, "delete from building_part where project_id = %s", (fresh["project"],)) == (True, 0)


def test_a_part_is_confirmed_only_through_gate1_confirm_by_a_designer(admin, world, fresh):
    admin.execute(PART, (fresh["firm"], fresh["project"], 0, "5"))
    pid = admin.execute("select id from building_part where project_id = %s", (fresh["project"],)).fetchone()[0]
    designer = world.firms["A"].users["designer"]
    call = "select gate1_confirm('building_part', array[%s]::uuid[])"
    assert not attempt(world.firms["A"].users["checker"], call, (pid,))[0]
    assert not attempt(designer, "update building_part set confirmed_by = %s, confirmed_at = now() where id = %s",
                       (designer, pid))[0]
    with as_user(designer) as cur:
        cur.execute(call, (pid,))
        cur.execute("select confirmed_by, area_m2_provenance::text from building_part where id = %s", (pid,))
        assert cur.fetchone() == (uuid.UUID(designer), "engineer_confirmed")
    with as_user(designer) as cur:      # an edit by the designer withdraws it (provenance reset to default as required)
        cur.execute("update building_part set building_class = '6', area_m2_provenance = 'default' where id = %s"
                    " returning confirmed_by", (pid,))
        assert cur.fetchone() == (None,)


def test_a_service_role_edit_of_a_confirmed_value_also_withdraws_the_confirmation(admin, world, fresh):
    iid = add_input(admin, fresh)
    designer = world.firms["A"].users["designer"]
    with as_user(designer) as cur:
        cur.execute(CONFIRM, (iid,))
        cur.connection.commit()
    assert confirmed_state(admin, iid) == ("engineer_confirmed", True)
    admin.execute("update system_input set value_number = 99 where id = %s", (iid,))   # service role / any other role
    assert confirmed_state(admin, iid) == ("engineer_confirmed", False)
    # untouched content keeps it
    with as_user(designer) as cur:
        cur.execute(CONFIRM, (iid,))
        cur.connection.commit()
    admin.execute("update system_input set source = 'api' where id = %s", (iid,))
    assert confirmed_state(admin, iid) == ("engineer_confirmed", True)


# ---- confirming review: project facts need a confirmation; positions and deletions withdraw part confirmations ----

@pytest.mark.parametrize("role", ["checker", "approver"])
def test_only_a_designer_changes_the_project_facts(admin, world, fresh, role):
    user = world.firms["A"].users[role]
    for sql in ("update project set climate_zone = 5 where id = %s", "update project set state = 'NSW' where id = %s",
                "update project set ncc_edition = 'NCC2022' where id = %s"):
        ok, changed = attempt(user, sql, (fresh["project"],))
        assert not ok or changed == 0, sql
    row = admin.execute("select climate_zone, state, ncc_edition from project where id = %s", (fresh["project"],)).fetchone()
    assert row == (6, "VIC", "NCC2025")


def test_project_facts_are_confirmed_only_by_a_designer_through_gate1_confirm(admin, world, fresh):
    designer = world.firms["A"].users["designer"]
    admin.execute("update project set approval_date = '2026-10-06' where id = %s", (fresh["project"],))
    call = "select gate1_confirm('project', array[%s]::uuid[])"
    assert not attempt(world.firms["A"].users["checker"], call, (fresh["project"],))[0]
    assert not attempt(designer, "update project set confirmed_by = %s, confirmed_at = now() where id = %s",
                       (designer, fresh["project"]))[0]
    with as_user(designer) as cur:
        cur.execute(call, (fresh["project"],))
        cur.execute("select confirmed_by from project where id = %s", (fresh["project"],))
        assert cur.fetchone() == (uuid.UUID(designer),)
    # another firm cannot confirm it
    assert not attempt(world.firms["B"].users["designer"], call, (fresh["project"],))[0]


def test_editing_a_confirmed_project_fact_withdraws_the_confirmation_for_every_role(admin, world, fresh):
    designer = world.firms["A"].users["designer"]
    admin.execute("update project set confirmed_by = %s, confirmed_at = now() where id = %s", (designer, fresh["project"]))
    admin.execute("update project set climate_zone = 5 where id = %s", (fresh["project"],))          # service role
    assert admin.execute("select confirmed_by from project where id = %s", (fresh["project"],)).fetchone() == (None,)
    admin.execute("update project set confirmed_by = %s, confirmed_at = now() where id = %s", (designer, fresh["project"]))
    admin.execute("update project set approval_date = '2026-10-06' where id = %s", (fresh["project"],))
    assert admin.execute("select confirmed_by from project where id = %s", (fresh["project"],)).fetchone() == (None,)


def test_reordering_or_deleting_parts_withdraws_their_confirmation(admin, world, fresh):
    designer = world.firms["A"].users["designer"]
    for pos, cls in ((0, "5"), (1, "2"), (2, "6")):
        admin.execute(PART, (fresh["firm"], fresh["project"], pos, cls))
    ids = [r[0] for r in admin.execute("select id from building_part where project_id = %s order by position",
                                       (fresh["project"],)).fetchall()]
    admin.execute("update building_part set confirmed_by = %s, confirmed_at = now() where project_id = %s",
                  (designer, fresh["project"]))
    admin.execute("update building_part set position = position + 100 where id = %s", (ids[1],))   # reorder
    state = dict(admin.execute("select id, confirmed_by is not null from building_part where project_id = %s",
                               (fresh["project"],)).fetchall())
    assert not any(state.values())      # a move shifts the indices subjects refer to: every part is unconfirmed again
    admin.execute("update building_part set confirmed_by = %s, confirmed_at = now() where project_id = %s",
                  (designer, fresh["project"]))
    admin.execute("delete from building_part where id = %s", (ids[1],))
    assert admin.execute("select count(*) from building_part where project_id = %s and confirmed_by is not null",
                         (fresh["project"],)).fetchone() == (0,)
