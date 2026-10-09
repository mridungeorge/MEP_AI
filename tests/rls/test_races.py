"""Two-session races: the edition and freeze guards must hold when transactions overlap.

These commit real rows (a throwaway firm per test) and delete them afterwards.
"""
import json
import threading
import time

import psycopg
import pytest

from tests.rls.conftest import DB_URL, uid


@pytest.fixture
def mini(admin):
    """A throwaway firm with one designer, an NCC2022 project and an open revision."""
    f, user, project, rev = uid(), uid(), uid(), uid()
    admin.execute("insert into firm (id, name) values (%s, 'race')", (f,))
    admin.execute("insert into auth.users (id, email) values (%s, %s)", (user, f"race-{user}@test.invalid"))
    admin.execute("insert into app_user (id, firm_id, role) values (%s, %s, 'designer')", (user, f))
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, building_class, ncc_edition)"
                  " values (%s, %s, 'race', 'VIC', 6, '5', 'NCC2022')", (project, f))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'R')",
                  (rev, f, project))
    yield {"firm": f, "user": user, "project": project, "revision": rev}
    admin.execute("delete from rule_result where firm_id = %s", (f,))
    admin.execute("update revision set frozen_at = null where firm_id = %s", (f,))
    for table in ("equipment", "space", "system", "revision", "project", "app_user"):
        admin.execute(f"delete from {table} where firm_id = %s", (f,))
    admin.execute("delete from auth.users where id = %s", (user,))
    try:      # the ledger is append-only and references the firm: a firm that has ledger entries stays (it is a throwaway)
        admin.execute("delete from firm where id = %s", (f,))
    except psycopg.errors.ForeignKeyViolation:
        pass


def client_conn(user):
    conn = psycopg.connect(DB_URL, autocommit=False)
    conn.execute("set local role authenticated")
    conn.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": user, "role": "authenticated"}),))
    return conn


def run_in_thread(fn):
    box = {}

    def target():
        try:
            box["result"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    return t, box


def test_edition_cannot_change_while_a_result_is_being_written(mini):
    engine = psycopg.connect(DB_URL, autocommit=False)
    engine.execute(
        "insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
        " values (%s, %s, 'NCC2022-J6D3-econ-cycle', 'NCC2022', 'FAIL', '{}', '{}')",
        (mini["firm"], mini["revision"]))  # not committed yet

    def designer_changes_edition():
        conn = client_conn(mini["user"])
        try:
            conn.execute("update project set ncc_edition = 'NCC2025' where id = %s", (mini["project"],))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(designer_changes_edition)
    time.sleep(0.8)
    assert t.is_alive(), "the edition change must wait for the in-flight result, not slip past it"
    engine.commit()
    t.join(10)
    engine.close()
    assert isinstance(box.get("error"), psycopg.errors.RaiseException), box


def test_result_cannot_be_written_while_the_edition_is_being_changed(admin, mini):
    designer = client_conn(mini["user"])
    designer.execute("update project set ncc_edition = 'NCC2025' where id = %s", (mini["project"],))  # uncommitted

    def engine_writes():
        conn = psycopg.connect(DB_URL, autocommit=False)
        try:
            conn.execute(
                "insert into rule_result (firm_id, revision_id, rule_id, edition, result, inputs, citation)"
                " values (%s, %s, 'NCC2022-J6D3-econ-cycle', 'NCC2022', 'FAIL', '{}', '{}')",
                (mini["firm"], mini["revision"]))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(engine_writes)
    time.sleep(0.8)
    assert t.is_alive()
    designer.commit()
    designer.close()
    t.join(10)
    assert isinstance(box.get("error"), psycopg.errors.RaiseException), box
    n = admin.execute("select count(*) from rule_result where firm_id = %s", (mini["firm"],)).fetchone()[0]
    assert n == 0


def test_freeze_waits_for_in_flight_input_writes(admin, mini):
    designer = client_conn(mini["user"])
    designer.execute("insert into space (firm_id, revision_id, name) values (%s, %s, 'late')",
                     (mini["firm"], mini["revision"]))  # autosave in flight, uncommitted

    def server_freezes():
        conn = psycopg.connect(DB_URL, autocommit=False)
        try:
            conn.execute("update revision set frozen_at = now() where id = %s", (mini["revision"],))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(server_freezes)
    time.sleep(0.8)
    assert t.is_alive(), "the freeze must wait for the in-flight write so the frozen set is complete"
    designer.commit()
    designer.close()
    t.join(10)
    assert "error" not in box
    assert admin.execute("select count(*) from space where revision_id = %s", (mini["revision"],)).fetchone()[0] == 1


def test_input_write_waits_for_an_in_flight_freeze_then_is_refused(admin, mini):
    server = psycopg.connect(DB_URL, autocommit=False)
    server.execute("update revision set frozen_at = now() where id = %s", (mini["revision"],))  # uncommitted

    def designer_writes():
        conn = client_conn(mini["user"])
        try:
            conn.execute("insert into space (firm_id, revision_id, name) values (%s, %s, 'too late')",
                         (mini["firm"], mini["revision"]))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(designer_writes)
    time.sleep(0.8)
    assert t.is_alive()
    server.commit()
    server.close()
    t.join(10)
    assert isinstance(box.get("error"), psycopg.errors.RaiseException), box
    assert admin.execute("select count(*) from space where revision_id = %s", (mini["revision"],)).fetchone()[0] == 0


def test_project_facts_cannot_change_beside_a_freeze(admin, mini):
    server = psycopg.connect(DB_URL, autocommit=False)
    server.execute("update revision set frozen_at = now() where id = %s", (mini["revision"],))  # uncommitted

    def designer_changes_facts():
        conn = client_conn(mini["user"])
        try:
            conn.execute("update project set climate_zone = 7 where id = %s", (mini["project"],))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(designer_changes_facts)
    time.sleep(0.8)
    assert t.is_alive(), "the project fact change must wait for the in-flight freeze"
    server.commit()
    server.close()
    t.join(10)
    assert isinstance(box.get("error"), psycopg.errors.RaiseException), box
    assert admin.execute("select climate_zone from project where id = %s", (mini["project"],)).fetchone()[0] == 6


def test_freeze_waits_for_an_in_flight_project_fact_change(admin, mini):
    designer = client_conn(mini["user"])
    designer.execute("update project set climate_zone = 7 where id = %s", (mini["project"],))  # uncommitted

    def server_freezes():
        conn = psycopg.connect(DB_URL, autocommit=False)
        try:
            conn.execute("update revision set frozen_at = now() where id = %s", (mini["revision"],))
            conn.commit()
        finally:
            conn.close()

    t, box = run_in_thread(server_freezes)
    time.sleep(0.8)
    assert t.is_alive(), "the freeze must wait so a revision is never frozen under changing facts"
    designer.commit()
    designer.close()
    t.join(10)
    assert "error" not in box
    assert admin.execute("select climate_zone from project where id = %s", (mini["project"],)).fetchone()[0] == 7
