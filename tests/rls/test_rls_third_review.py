"""Third-review attacks: search_path shadowing, parent revisions, provenance, artifact reads."""
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
def frozen_rev(admin, world):
    a = world.firms["A"]
    rev = uid()
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'T')",
                  (rev, a.id, a.project))
    admin.execute("update revision set frozen_at = now() where id = %s", (rev,))
    return rev


def test_temp_table_cannot_shadow_trigger_lookups(frozen_rev, world):
    a = world.firms["A"]
    user = a.users["designer"]
    with as_user(user) as cur:
        cur.execute("create temp table revision (id uuid, frozen_at timestamptz)")
        with pytest.raises(BLOCKED):
            cur.execute("insert into space (firm_id, revision_id) values (%s, %s)", (a.id, frozen_rev))


def test_parent_revision_is_same_project_and_immutable(actor, admin):
    _, user, mine = actor
    other_project = uid()
    admin.execute("insert into project (id, firm_id, address, state, building_class, ncc_edition)"
                  " values (%s, %s, 'p2', 'VIC', '5', 'NCC2025')", (other_project, mine.id))
    foreign_rev = uid()
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'X')",
                  (foreign_rev, mine.id, other_project))
    with as_user(user) as cur, pytest.raises(BLOCKED):  # parent in another project (other edition)
        cur.execute("insert into revision (firm_id, project_id, architect_rev, parent_revision_id)"
                    " values (%s, %s, 'C', %s)", (mine.id, mine.project, foreign_rev))
    with as_user(user) as cur, pytest.raises(BLOCKED):  # nor re-pointed later, nor to itself
        cur.execute("update revision set parent_revision_id = %s where id = %s", (mine.revision, mine.revision))


def test_project_facts_lock_when_a_revision_is_frozen(admin, world, frozen_rev):
    a = world.firms["A"]
    fresh = uid()
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, building_class, ncc_edition)"
                  " values (%s, %s, 'fz', 'VIC', 6, '5', 'NCC2022')", (fresh, a.id))
    rev = uid()
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'F')",
                  (rev, a.id, fresh))
    admin.execute("update revision set frozen_at = now() where id = %s", (rev,))
    with as_user(a.users["designer"]) as cur, pytest.raises(BLOCKED):
        cur.execute("update project set climate_zone = 1 where id = %s", (fresh,))


def test_client_cannot_assign_earned_provenance_or_units(actor):
    _, user, mine = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("insert into space (firm_id, revision_id, area_m2_value, area_m2_provenance)"
                    " values (%s, %s, 5, 'engineer_confirmed')", (mine.id, mine.revision))
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("insert into system (firm_id, revision_id, type, capacity_kw_value, capacity_kw_unit)"
                    " values (%s, %s, 'exhaust', 5, 'furlong')", (mine.id, mine.revision))
    with as_user(user) as cur:  # default provenance is allowed
        cur.execute("insert into space (firm_id, revision_id, area_m2_value, area_m2_provenance)"
                    " values (%s, %s, 5, 'default')", (mine.id, mine.revision))
        assert cur.rowcount == 1


def test_clients_cannot_read_unreleased_artifacts_or_share_tokens(actor):
    _, user, mine = actor
    with as_user(user) as cur:
        cur.execute("select count(*) from artifact where id = %s", (mine.open_artifact,))
        assert cur.fetchone()[0] == 0
        cur.execute("select count(*) from artifact where id = %s", (mine.released_artifact,))
        assert cur.fetchone()[0] == 1
    with as_user(user) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("select token from ledger_link")


def test_review_decision_is_a_closed_set(admin, world):
    a = world.firms["A"]
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("insert into review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason)"
                      " values (%s, %s, %s, %s, %s, 'PASS', 'a reason')",
                      (a.id, a.rule_result, a.revision, GATE_FOR["designer"], a.users["designer"]))


def test_stored_results_cannot_be_rewritten_even_by_the_service_role(admin, world):
    a = world.firms["A"]
    for sql in ("update rule_result set result = 'PASS' where id = %s",
                "update rule_result set inputs = '{\"x\": 1}' where id = %s",
                "update rule_result set citation = '{\"x\": 1}' where id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, (a.rule_result,))
    admin.execute("update rule_result set stale = true where id = %s", (a.rule_result,))  # staleness is allowed
    admin.execute("update rule_result set stale = false where id = %s", (a.rule_result,))


def test_definer_triggers_do_not_reveal_other_firms_rows(world, frozen_rev, admin):
    """A designer supplying another firm's firm_id learns nothing: frozen, open and unknown
    revisions all give the same row-level-security refusal and take no lock."""
    a, b = world.firms["A"], world.firms["B"]
    designer_b = b.users["designer"]
    probes = (frozen_rev, a.revision, uid())  # frozen A revision, open A revision, unknown
    for rev in probes:
        with as_user(designer_b) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege) as err:
            cur.execute("insert into space (firm_id, revision_id) values (%s, %s)", (a.id, rev))
        assert "revision is frozen" not in str(err.value)
    with as_user(designer_b) as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute("insert into revision (firm_id, project_id, architect_rev, parent_revision_id)"
                    " values (%s, %s, 'Z', %s)", (a.id, a.project, a.revision))
