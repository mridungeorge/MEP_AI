"""Second-review attacks: frozen revisions, edition mixing, privileges, review integrity."""
import psycopg
import pytest

from tests.rls.conftest import GATE_FOR, ROLES, as_user, uid

BLOCKED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.NotNullViolation,
           psycopg.errors.CheckViolation, psycopg.errors.RaiseException,
           psycopg.errors.ForeignKeyViolation)
COMBOS = [(f, r) for f in ("A", "B") for r in ROLES]


@pytest.fixture(params=COMBOS, ids=[f"{f}-{r}" for f, r in COMBOS])
def actor(request, world):
    label, role = request.param
    mine = world.firms[label]
    return role, mine.users[role], mine


@pytest.fixture
def frozen(admin, world):
    """A frozen revision in firm A with a space, a system and equipment (built by the service role)."""
    a = world.firms["A"]
    rev, space, system, equip = uid(), uid(), uid(), uid()
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'F')",
                  (rev, a.id, a.project))
    admin.execute("insert into space (id, firm_id, revision_id, name) values (%s, %s, %s, 'room')",
                  (space, a.id, rev))
    admin.execute("insert into system (id, firm_id, revision_id, type) values (%s, %s, %s, 'exhaust')",
                  (system, a.id, rev))
    admin.execute("insert into equipment (id, firm_id, system_id, tag) values (%s, %s, %s, 'E1')",
                  (equip, a.id, system))
    admin.execute("update revision set frozen_at = now() where id = %s", (rev,))
    return {"revision": rev, "space": space, "system": system, "equipment": equip, "firm": a.id}


def test_frozen_revision_inputs_cannot_change(actor, frozen):
    _, user, _mine = actor
    attacks = (
        ("insert into space (firm_id, revision_id) values (%s, %s)", (frozen["firm"], frozen["revision"])),
        ("update system set capacity_kw_value = 999, capacity_kw_provenance = 'extracted' where id = %s",
         (frozen["system"],)),
        ("update space set name = 'x' where id = %s", (frozen["space"],)),
        ("delete from space where id = %s", (frozen["space"],)),
        ("update equipment set tag = 'x' where id = %s", (frozen["equipment"],)),
    )
    for sql, params in attacks:
        with as_user(user) as cur:
            try:
                cur.execute(sql, params)
            except BLOCKED:
                continue
            assert cur.rowcount == 0, sql  # not visible / no policy: nothing changed


def test_frozen_revision_blocks_the_service_role_too(admin, frozen):
    cases = (
        ("insert into space (firm_id, revision_id) values (%s, %s)", (frozen["firm"], frozen["revision"])),
        ("update system set capacity_kw_value = 1 where id = %s", (frozen["system"],)),
        ("delete from equipment where id = %s", (frozen["equipment"],)),
    )
    for sql, params in cases:
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, params)


def test_client_cannot_freeze_or_set_status(actor):
    _, user, mine = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("update revision set frozen_at = now() where id = %s", (mine.revision,))
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("update revision set status = 'approved' where id = %s", (mine.revision,))
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("insert into revision (firm_id, project_id, architect_rev, status) values (%s, %s, 'X', 'approved')",
                    (mine.id, mine.project))


def test_revision_cannot_move_between_projects_or_editions(actor, admin):
    _, user, mine = actor
    other_project = uid()
    admin.execute("insert into project (id, firm_id, address, state, building_class, ncc_edition)"
                  " values (%s, %s, '2 Test St', 'VIC', '5', 'NCC2025')", (other_project, mine.id))
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("update revision set project_id = %s where id = %s", (other_project, mine.revision))
    other_rev = uid()
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'M')",
                  (other_rev, mine.id, mine.project))
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("update system set revision_id = %s where id = %s", (other_rev, mine.system))


def test_project_facts_freeze_once_results_exist(actor):
    role, user, mine = actor
    for col, val in (("state", "'NT'"), ("climate_zone", "1"), ("building_class", "'2'"),
                     ("ncc_edition", "'NCC2025'")):
        with as_user(user) as cur:
            try:
                cur.execute(f"update project set {col} = {val} where id = %s", (mine.project,))
            except BLOCKED:
                continue                         # the freeze trigger stopped a designer
            # only a designer may write project facts: for other roles the row is simply not updatable
            assert role != "designer" and cur.rowcount == 0, col


def test_review_must_be_attached_to_a_result(actor):
    role, user, mine = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("insert into review (firm_id, gate, user_id, decision) values (%s, %s, %s, 'approve')",
                    (mine.id, GATE_FOR[role], user))


def test_closed_domains(admin, world):
    mine = world.firms["A"]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("insert into system (firm_id, revision_id, type) values (%s, %s, 'AC')",
                      (mine.id, mine.revision))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("insert into project (firm_id, address, state, building_class, ncc_edition)"
                      " values (%s, 'x', 'VIC', '7A', 'NCC2022')", (mine.id,))


def test_dangerous_privileges_revoked(admin):
    for role in ("anon", "authenticated"):
        for priv in ("TRUNCATE", "TRIGGER", "REFERENCES"):
            for table in ("rule_result", "artifact", "ledger_event", "project"):
                row = admin.execute("select has_table_privilege(%s, %s, %s)",
                                    (role, f"public.{table}", priv)).fetchone()
                assert row[0] is False, (role, priv, table)
    for table in ("rule_result", "artifact", "ledger_event", "ledger_link", "app_user", "firm"):
        for priv in ("INSERT", "UPDATE", "DELETE"):
            row = admin.execute("select has_table_privilege('authenticated', %s, %s)",
                                (f"public.{table}", priv)).fetchone()
            assert row[0] is False, (priv, table)
    assert admin.execute("select has_table_privilege('anon', 'public.project', 'SELECT')").fetchone()[0] is False


def test_client_cannot_truncate_or_attach_triggers(actor):
    _, user, _ = actor
    with as_user(user) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("truncate rule_result cascade")
    with as_user(user) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("create trigger evil before insert on rule_result for each row"
                    " execute function ledger_event_immutable()")
