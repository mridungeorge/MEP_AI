"""Fixtures for the RLS attack tests. Needs a local Supabase DB with migrations applied
(scripts/ci.sh does `supabase start` + `supabase db reset`). Fails loudly if it is down."""
import contextlib
import json
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

import psycopg
import pytest

os.environ.setdefault("MEP_RATELIMIT", "off")          # the attack tests make many requests as one client; the limiter has its own tests

DB_URL = os.environ.get("MEP_TEST_DB_URL", "postgresql://postgres:postgres@127.0.0.1:54322/postgres")
ROLES = ("designer", "checker", "approver")
GATE_FOR = {"designer": "gate1", "checker": "gate2", "approver": "gate3"}
PASSED = json.dumps({"passed": True})
FAILED = json.dumps({"passed": False})


@dataclass
class Firm:
    id: str
    users: dict[str, str] = field(default_factory=dict)  # role -> user id
    project: str = ""
    revision: str = ""
    system: str = ""
    rule_result: str = ""
    released_artifact: str = ""
    open_artifact: str = ""


@dataclass
class World:
    firms: dict[str, Firm]


def uid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def admin():
    """Superuser/service connection (bypasses RLS) used to build fixtures and as the service role."""
    conn = psycopg.connect(DB_URL, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def world(admin) -> World:
    firms: dict[str, Firm] = {}
    for label in ("A", "B"):
        f = Firm(id=uid(), project=uid(), revision=uid(), system=uid(), rule_result=uid(),
                 released_artifact=uid(), open_artifact=uid())
        admin.execute("insert into firm (id, name) values (%s, %s)", (f.id, f"firm {label}"))
        for role in ROLES:
            u = uid()
            f.users[role] = u
            admin.execute("insert into auth.users (id, email) values (%s, %s)", (u, f"{role}-{u}@test.invalid"))
            admin.execute("insert into app_user (id, firm_id, role) values (%s, %s, %s)", (u, f.id, role))
        admin.execute(
            "insert into project (id, firm_id, address, state, climate_zone, building_class, ncc_edition)"
            " values (%s, %s, '1 Test St', 'VIC', 6, '5', 'NCC2022')", (f.project, f.id))
        admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                      (f.revision, f.id, f.project))
        admin.execute("insert into system (id, firm_id, revision_id, type) values (%s, %s, %s, 'air_conditioning')",
                      (f.system, f.id, f.revision))
        admin.execute(
            "insert into rule_result (id, firm_id, revision_id, rule_id, edition, result, inputs, citation)"
            " values (%s, %s, %s, 'NCC2022-J6D3-econ-cycle', 'NCC2022', 'FAIL', '{}', '{}')",
            (f.rule_result, f.id, f.revision))
        admin.execute(
            "insert into artifact (id, firm_id, revision_id, kind, path, checksum, validator, released)"
            " values (%s, %s, %s, 'dxf', 'a.dxf', 'abc', %s, true)",
            (f.released_artifact, f.id, f.revision, PASSED))
        admin.execute(
            "insert into artifact (id, firm_id, revision_id, kind, path, checksum, validator, released)"
            " values (%s, %s, %s, 'dxf', 'b.dxf', 'def', %s, false)",
            (f.open_artifact, f.id, f.revision, FAILED))
        admin.execute("insert into ledger_event (firm_id, revision_id, kind, payload) values (%s, %s, 'seed', '{}')",
                      (f.id, f.revision))
        firms[label] = f
    return World(firms)


@contextlib.contextmanager
def as_user(user_id: str) -> Iterator[psycopg.Cursor]:
    """Act as a Supabase `authenticated` client with this JWT subject; always rolled back."""
    conn = psycopg.connect(DB_URL, autocommit=False)
    try:
        cur = conn.cursor()
        cur.execute("set local role authenticated")
        cur.execute("select set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": user_id, "role": "authenticated"}),))
        yield cur
    finally:
        conn.rollback()
        conn.close()
