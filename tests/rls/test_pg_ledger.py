"""The Postgres ledger writer records a jurisdiction override as an append-only ledger_event."""
import json
import uuid

import pytest
from mep.engine.ledger import PostgresLedger

from tests.rls.conftest import DB_URL


def test_override_event_is_appended_with_its_payload():
    psycopg = pytest.importorskip("psycopg")
    firm, rev, proj = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    with psycopg.connect(DB_URL, autocommit=True) as admin:
        admin.execute("insert into firm (id, name) values (%s, 'ledger-test')", (firm,))
        admin.execute("insert into project (id, firm_id, address, state, building_class, ncc_edition)"
                      " values (%s, %s, 'x', 'NT', '5', 'NCC2025')", (proj, firm))
        admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                      (rev, firm, proj))
        PostgresLedger(admin).write("jurisdiction_override", {"user_id": "u", "reason": "a reason"},
                                    firm_id=firm, revision_id=rev)
        row = admin.execute("select kind, payload from ledger_event where firm_id = %s", (firm,)).fetchone()
        assert row[0] == "jurisdiction_override" and row[1]["reason"] == "a reason"
        with pytest.raises(psycopg.errors.RaiseException):  # append-only
            admin.execute("update ledger_event set kind = 'x' where firm_id = %s", (firm,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):  # a ledger row needs a real firm and revision
            PostgresLedger(admin).write("x", {}, firm_id=str(uuid.uuid4()), revision_id=rev)
    assert json.dumps({"ok": True})


def test_ledger_refuses_to_write_inside_a_callers_open_transaction():
    """A savepoint is not a durable commit: the override record must commit before any rule runs."""
    import psycopg

    firm = str(uuid.uuid4())
    with psycopg.connect(DB_URL, autocommit=False) as conn:
        conn.execute("select 1")  # opens a transaction
        with pytest.raises(RuntimeError):
            PostgresLedger(conn).write("jurisdiction_override", {}, firm_id=firm, revision_id="")
