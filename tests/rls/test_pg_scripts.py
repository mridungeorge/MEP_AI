"""The service scripts: demo seed, approver registration (with evidence in the ledger), signer mode, disputed-rule export.
They run for real against the local Supabase."""
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import jwt
import psycopg
import pytest

from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]
SECRET = os.environ.get("MEP_JWT_SECRET", "super-secret-jwt-token-with-at-least-32-characters-long")
SUPABASE_URL = os.environ.get("MEP_TEST_SUPABASE_URL", "http://127.0.0.1:54321")


def run(script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MEP_DB_URL": DB_URL, "MEP_JWT_SECRET": SECRET, "MEP_SUPABASE_URL": SUPABASE_URL,
           "MEP_SUPABASE_SERVICE_KEY": jwt.encode({"iss": "supabase-demo", "role": "service_role", "exp": int(time.time()) + 3600},
                                                  SECRET, algorithm="HS256")}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    assert (p.returncode == 0) == ok, p.stdout + p.stderr
    return p


@pytest.fixture
def demo(admin):
    tag = uuid.uuid4().hex[:8]
    emails = {r: f"{r}-{tag}@demo.invalid" for r in ("designer", "checker", "approver")}
    # the firm name is fixed by the script; a previous test run's firm is reused (idempotent), so use a fresh database per CI run
    p = run("seed_demo.py", "--designer", emails["designer"], "--checker", emails["checker"], "--approver", emails["approver"])
    return emails, p


def test_the_demo_seed_creates_the_firm_three_users_and_the_synthetic_project(admin, demo):
    emails, p = demo
    assert "firm " in p.stdout
    rows = admin.execute("select a.email, u.role::text, u.registration_no from app_user u join auth.users a on a.id = u.id where a.email = any(%s)",
                         (list(emails.values()),)).fetchall()
    assert {r[1] for r in rows} == {"designer", "checker", "approver"}
    assert [r[2] for r in rows if r[1] == "approver"] == ["DEMO-0001"]
    proj = admin.execute("select p.address, p.state, p.ncc_edition, p.climate_zone from project p join firm f on f.id = p.firm_id"
                         " where f.name = 'Demo Mechanical (synthetic)'").fetchall()
    assert proj and proj[0][1:] == ("VIC", "NCC2025", 6)
    systems = admin.execute("select count(*) from system s join revision r on r.id = s.revision_id join project p on p.id = r.project_id"
                            " where p.address like '%synthetic office%'").fetchone()[0]
    assert systems >= 4
    # nothing is confirmed: the Gate 1 walk-through is done live
    assert admin.execute("select count(*) from system_input i join system s on s.id = i.system_id join revision r on r.id = s.revision_id"
                         " join project p on p.id = r.project_id where p.address like '%synthetic office%' and i.confirmed_by is not null"
                         ).fetchone()[0] == 0
    again = run("seed_demo.py", "--designer", emails["designer"], "--checker", emails["checker"], "--approver", emails["approver"])
    assert "already exists" in again.stdout


def test_strict_mode_refuses_one_address_for_three_roles():
    p = run("seed_demo.py", "--designer", "x@demo.invalid", "--checker", "x@demo.invalid", "--approver", "x@demo.invalid", ok=False)
    assert "three different" in p.stdout + p.stderr


def test_registering_an_approver_needs_evidence_and_is_ledgered(admin, demo):
    emails, _ = demo
    e = emails["approver"]
    run("admin_users.py", "register-approver", "--email", e, "--number", "bad", "--register", "NER", "--verified-by", "x", "--evidence", "x", ok=False)
    run("admin_users.py", "register-approver", "--email", e, "--number", "RPEQ 20480", "--register", "RPEQ", "--verified-by", "A. Admin",
        "--evidence", "RPEQ register search 12 Oct 2026: name and number match, status current", ok=True)
    assert admin.execute("select registration_no from app_user u join auth.users a on a.id = u.id where a.email = %s", (e,)).fetchone()[0] == "RPEQ 20480"
    kinds = admin.execute("select kind, payload from ledger_event where kind in ('approver_registration_verified', 'app_user_changed')"
                          " and payload ->> 'registration_no' = 'RPEQ 20480' order by seq").fetchall()
    assert {k for k, _ in kinds} == {"app_user_changed", "approver_registration_verified"}
    verified = next(p for k, p in kinds if k == "approver_registration_verified")
    assert verified["register"] == "RPEQ" and verified["previous"] == "DEMO-0001" and "name and number match" in verified["evidence"]
    run("admin_users.py", "register-approver", "--email", emails["designer"], "--number", "RPEQ 1111", "--register", "RPEQ",
        "--verified-by", "A. Admin", "--evidence", "a designer is not an approver, so this must be refused", ok=False)


def test_signer_mode_and_extra_roles_through_the_script(admin, demo):
    emails, _ = demo
    run("admin_users.py", "set-signer-mode", "--firm", "Demo Mechanical (synthetic)", "--mode", "strict")
    run("admin_users.py", "grant-roles", "--email", emails["approver"], "--roles", "designer,checker", ok=False)         # strict: refused
    run("admin_users.py", "set-signer-mode", "--firm", "Demo Mechanical (synthetic)", "--mode", "small_firm")
    out = run("admin_users.py", "grant-roles", "--email", emails["approver"], "--roles", "designer,checker").stdout
    assert "checker, designer" in out
    assert "NOT INDEPENDENTLY CHECKED" in run("admin_users.py", "set-signer-mode", "--firm", "Demo Mechanical (synthetic)", "--mode", "small_firm").stdout
    run("admin_users.py", "set-signer-mode", "--firm", "Demo Mechanical (synthetic)", "--mode", "strict")
    assert admin.execute("select signer_mode from firm where name = 'Demo Mechanical (synthetic)'").fetchone()[0] == "strict"
    assert admin.execute("select also_roles from app_user u join auth.users a on a.id = u.id where a.email = %s", (emails["approver"],)).fetchone()[0] in ([], "{}")
    with psycopg.connect(DB_URL) as c:
        assert c.execute("select count(*) from ledger_event where kind = 'firm_signer_mode_changed'").fetchone()[0] >= 2


def test_the_disputed_export_matches_the_table(admin, tmp_path):
    out = tmp_path / "disputed.md"
    p = run("export_disputed.py", "--out", str(out))
    n = admin.execute("select count(*) from rule_dispute").fetchone()[0]
    assert f"{n} dispute(s)" in p.stdout and out.read_text(encoding="utf-8").startswith("# Disputed rules")
