"""Each attack from the Sprint 0 adversarial review, run as every (firm, role) combination."""
import json

import psycopg
import pytest

from tests.rls.conftest import FAILED, GATE_FOR, ROLES, as_user

BLOCKED = (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation,
           psycopg.errors.RaiseException, psycopg.errors.ForeignKeyViolation)
COMBOS = [(f, r) for f in ("A", "B") for r in ROLES]


@pytest.fixture(params=COMBOS, ids=[f"{f}-{r}" for f, r in COMBOS])
def actor(request, world):
    label, role = request.param
    mine = world.firms[label]
    theirs = world.firms["B" if label == "A" else "A"]
    return role, mine.users[role], mine, theirs


def no_effect(cur, sql, params):
    """The write must not land: either refused outright or matching zero rows."""
    try:
        cur.execute(sql, params)
    except BLOCKED:
        return
    assert cur.rowcount == 0, sql


def test_positive_control_reads_own_firm(actor):
    _, user, mine, _ = actor
    with as_user(user) as cur:
        cur.execute("select count(*) from project where id = %s", (mine.project,))
        assert cur.fetchone()[0] == 1
        cur.execute("select count(*) from rule_result where id = %s", (mine.rule_result,))
        assert cur.fetchone()[0] == 1


def test_client_cannot_insert_a_pass(actor):
    _, user, mine, _ = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute(
            "insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
            " values (%s, %s, 'NCC2022-J6D3-econ-cycle', 'NCC2022', 'PASS', '{}', '{}')",
            (mine.id, mine.revision))


def test_client_cannot_flip_a_fail_to_pass(actor, admin):
    _, user, mine, _ = actor
    for sql in ("update rule_result set result = 'PASS' where id = %s", "delete from rule_result where id = %s"):
        with as_user(user) as cur:
            no_effect(cur, sql, (mine.rule_result,))
    row = admin.execute("select result from rule_result where id = %s", (mine.rule_result,)).fetchone()
    assert row[0] == "FAIL"


def test_role_escalation_blocked(actor, admin):
    role, user, mine, _ = actor
    for sql, params in (("update app_user set role = 'approver' where id = %s", (user,)),
                        ("update firm set sample_size = 1 where id = %s", (mine.id,))):
        with as_user(user) as cur:
            no_effect(cur, sql, params)
    assert admin.execute("select role from app_user where id = %s", (user,)).fetchone()[0] == role


def test_forged_signoff_blocked(actor):
    role, user, mine, _ = actor
    for r, gate in GATE_FOR.items():  # signing a gate the role does not hold
        if r == role:
            continue
        with as_user(user) as cur, pytest.raises(BLOCKED):
            cur.execute("insert into signoff (firm_id, revision_id, gate, user_id) values (%s, %s, %s, %s)",
                        (mine.id, mine.revision, gate, user))
    for victim_role, victim in mine.users.items():  # signing or reviewing as someone else
        if victim == user:
            continue
        with as_user(user) as cur, pytest.raises(BLOCKED):
            cur.execute("insert into signoff (firm_id, revision_id, gate, user_id) values (%s, %s, %s, %s)",
                        (mine.id, mine.revision, GATE_FOR[victim_role], victim))
        with as_user(user) as cur, pytest.raises(BLOCKED):
            cur.execute("insert into review (firm_id, rule_result_id, gate, user_id, decision)"
                        " values (%s, %s, %s, %s, 'approve')",
                        (mine.id, mine.rule_result, GATE_FOR[victim_role], victim))


def test_no_client_can_write_a_signoff_or_a_review_directly(actor):
    """Phase 4a: sign-offs and reviews go only through sign_gate / gate2_* (which check role, order and state)."""
    role, user, mine, theirs = actor
    for firm in (mine, theirs):
        with as_user(user) as cur, pytest.raises(BLOCKED):
            cur.execute("insert into signoff (firm_id, revision_id, gate, user_id) values (%s, %s, %s, %s)",
                        (firm.id, firm.revision, GATE_FOR[role], user))
        with as_user(user) as cur, pytest.raises(BLOCKED):
            cur.execute("insert into review (firm_id, rule_result_id, revision_id, gate, user_id, decision, reason)"
                        " values (%s, %s, %s, %s, %s, 'approve', 'looks fine')",
                        (firm.id, firm.rule_result, firm.revision, GATE_FOR[role], user))


def test_cross_firm_read_blocked(actor):
    _, user, _, theirs = actor
    checks = (("project", "id", theirs.project), ("revision", "id", theirs.revision),
              ("system", "id", theirs.system), ("rule_result", "id", theirs.rule_result),
              ("artifact", "id", theirs.released_artifact), ("app_user", "firm_id", theirs.id),
              ("firm", "id", theirs.id), ("ledger_event", "firm_id", theirs.id))
    with as_user(user) as cur:
        for table, col, val in checks:
            cur.execute(f"select count(*) from {table} where {col} = %s", (val,))
            assert cur.fetchone()[0] == 0, table


def test_cross_firm_write_blocked(actor, admin):
    _, user, mine, theirs = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):  # claim the other firm's id
        cur.execute("insert into project (firm_id, address, state, ncc_edition) values (%s, 'x', 'VIC', 'NCC2022')",
                    (theirs.id,))
    with as_user(user) as cur, pytest.raises(BLOCKED):  # own firm_id, other firm's revision (composite FK)
        cur.execute("insert into space (firm_id, revision_id) values (%s, %s)", (mine.id, theirs.revision))
    with as_user(user) as cur:  # invisible rows cannot be updated
        cur.execute("update project set address = 'pwned' where id = %s", (theirs.project,))
        assert cur.rowcount == 0
    with as_user(user) as cur:  # nor can a row be handed to the other firm (non-designers cannot update projects at all)
        try:
            cur.execute("update project set firm_id = %s where id = %s", (theirs.id, mine.project))
        except BLOCKED:
            pass
        else:
            assert cur.rowcount == 0
    row = admin.execute("select address from project where id = %s", (theirs.project,)).fetchone()
    assert row[0] == "1 Test St"


def test_service_role_cannot_cross_firms_via_foreign_keys(admin, world):
    a, b = world.firms["A"], world.firms["B"]
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute("insert into space (firm_id, revision_id) values (%s, %s)", (a.id, b.revision))


def test_released_artifact_cannot_be_edited(actor, admin):
    _, user, mine, _ = actor
    with as_user(user) as cur:  # clients have no write path to artifacts at all
        no_effect(cur, "update artifact set checksum = 'evil' where id = %s", (mine.released_artifact,))
    with as_user(user) as cur:
        no_effect(cur, "update artifact set released = true, validator = %s where id = %s",
                  (json.dumps({"passed": True}), mine.open_artifact))
    edits = ("update artifact set path = 'swapped.dxf' where id = %s",
             "update artifact set checksum = 'evil' where id = %s",
             "update artifact set validator = '{}' where id = %s",
             "update artifact set released = false where id = %s")
    for sql in edits:  # even the service role is stopped
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, (mine.released_artifact,))


def test_artifact_cannot_be_released_without_a_passing_validator(admin, world):
    mine = world.firms["A"]
    for bad in (FAILED, '{"passed": "true"}', "{}"):
        with pytest.raises(psycopg.errors.CheckViolation):
            admin.execute("update artifact set released = true, validator = %s where id = %s",
                          (bad, mine.open_artifact))


def test_ledger_is_append_only_and_client_cannot_write(actor, admin):
    _, user, mine, _ = actor
    with as_user(user) as cur, pytest.raises(BLOCKED):
        cur.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'forged', '{}')",
                    (mine.id, mine.revision))
    for sql in ("update ledger_event set kind = 'x' where firm_id = %s",
                "delete from ledger_event where firm_id = %s"):
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, (mine.id,))
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("truncate ledger_event")


def test_one_edition_per_project(admin, world):
    mine = world.firms["A"]
    insert = ("insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
              " values (%s, %s, %s, %s, 'PASS', '{}', '{}')")
    with pytest.raises(psycopg.errors.RaiseException):  # result edition must match the project's edition
        admin.execute(insert, (mine.id, mine.revision, "NCC2025-J6D3-econ-cycle", "NCC2025"))
    with pytest.raises(psycopg.errors.CheckViolation):  # rule id must start with the result's edition
        admin.execute(insert, (mine.id, mine.revision, "NCC2025-J6D3-econ-cycle", "NCC2022"))
    with pytest.raises(psycopg.errors.RaiseException):  # edition frozen once results exist
        admin.execute("update project set ncc_edition = 'NCC2025' where id = %s", (mine.project,))


def test_no_client_write_policy_on_server_written_tables(admin):
    rows = admin.execute("select tablename, cmd from pg_policies where schemaname = 'public'").fetchall()
    for table in ("rule_result", "artifact", "ledger_event", "ledger_link", "app_user", "firm"):
        assert {c for t, c in rows if t == table} == {"SELECT"}, table
