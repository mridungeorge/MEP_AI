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


def run(script: str, *args: str, ok: bool = True, says: str = "") -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MEP_DB_URL": DB_URL, "MEP_JWT_SECRET": SECRET, "MEP_SUPABASE_URL": SUPABASE_URL,
           "MEP_SUPABASE_SERVICE_KEY": jwt.encode({"iss": "supabase-demo", "role": "service_role", "exp": int(time.time()) + 3600},
                                                  SECRET, algorithm="HS256")}
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True, env=env, cwd=ROOT, check=False)
    assert (p.returncode == 0) == ok, p.stdout + p.stderr
    if not ok:
        assert "Traceback" not in p.stderr, p.stderr           # a refusal is a message, never a crash
        assert says in p.stdout + p.stderr, p.stdout + p.stderr
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
    run("admin_users.py", "register-approver", "--email", e, "--number", "bad", "--register", "NER", "--verified-by", "x", "--evidence", "x", ok=False, says="--evidence must say")
    run("admin_users.py", "register-approver", "--email", e, "--number", "RPEQ 20480", "--register", "RPEQ", "--verified-by", "A. Admin",
        "--evidence", "RPEQ register search 12 Oct 2026: name and number match, status current", ok=True)
    assert admin.execute("select registration_no from app_user u join auth.users a on a.id = u.id where a.email = %s", (e,)).fetchone()[0] == "RPEQ 20480"
    kinds = admin.execute("select kind, payload from ledger_event where kind in ('approver_registration_verified', 'app_user_changed')"
                          " and payload ->> 'registration_no' = 'RPEQ 20480' order by seq").fetchall()
    assert {k for k, _ in kinds} == {"app_user_changed", "approver_registration_verified"}
    verified = next(p for k, p in kinds if k == "approver_registration_verified")
    assert verified["register"] == "RPEQ" and verified["previous"] == "DEMO-0001" and "name and number match" in verified["evidence"]
    run("admin_users.py", "register-approver", "--email", emails["designer"], "--number", "RPEQ 1111", "--register", "RPEQ",
        "--verified-by", "A. Admin", "--evidence", "a designer is not an approver, so this must be refused", ok=False, says="only an approver")


def test_signer_mode_and_extra_roles_through_the_script(admin, demo):
    emails, _ = demo
    run("admin_users.py", "set-signer-mode", "--firm", "Demo Mechanical (synthetic)", "--mode", "strict")
    run("admin_users.py", "grant-roles", "--email", emails["approver"], "--roles", "designer,checker", ok=False, says="small_firm firm")         # strict: refused
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


def test_one_person_alone_gets_one_approver_account_that_acts_as_the_other_roles(admin):
    tag = uuid.uuid4().hex[:8]
    e = f"solo-{tag}@demo.invalid"
    name = f"Demo Mechanical (synthetic) solo {tag}"
    run("seed_demo.py", "--designer", e, "--checker", e, "--approver", e, ok=False, says="strict mode needs three different")      # strict refuses one address
    run("seed_demo.py", "--designer", e, "--checker", e, "--approver", e, "--mode", "small_firm", "--firm-name", name)
    rows = admin.execute("select u.role::text, u.also_roles::text, u.registration_no from app_user u join firm f on f.id = u.firm_id"
                         " where f.name = %s", (name,)).fetchall()
    assert rows == [("approver", "{designer,checker}", "DEMO-0001")]
    run("seed_demo.py", "--designer", e, "--checker", e, "--approver", e, "--mode", "small_firm", "--firm-name", "Not the demo", ok=False, says="must start with")


def test_onboarding_a_real_firm_is_scripted_and_ledgered(admin):
    tag = uuid.uuid4().hex[:8]
    firm, e = f"Onboard Pty Ltd {tag}", f"jo-{tag}@onboard.invalid"
    run("admin_users.py", "add-firm", "--name", firm)
    run("admin_users.py", "add-firm", "--name", firm, ok=False, says="already exists")
    run("admin_users.py", "add-firm", "--name", "Demo Mechanical (synthetic) Evil", ok=False, says="reserved")
    run("admin_users.py", "add-user", "--firm", firm, "--email", e, "--role", "designer")
    run("admin_users.py", "add-user", "--firm", firm, "--email", e, "--role", "checker", ok=False, says="already belongs to a firm")
    run("admin_users.py", "set-role", "--email", e, "--role", "approver")
    run("admin_users.py", "register-approver", "--email", e, "--number", "NER 4455667", "--register", "NER", "--verified-by", "A. Admin",
        "--evidence", "NER search 12 Oct 2026: name and number match, current, mechanical")
    out = run("admin_users.py", "show", "--firm", firm).stdout
    assert e in out and "approver" in out and "NER 4455667" in out
    kinds = {r[0] for r in admin.execute("select e.kind from ledger_event e join firm f on f.id = e.firm_id where f.name = %s", (firm,))}
    assert {"firm_created", "app_user_changed", "approver_registration_verified"} <= kinds
    admin.execute("insert into firm (name) values (%s)", (firm,))                                                   # a duplicate name
    run("admin_users.py", "show", "--firm", firm, ok=False, says="refusing to guess")


def test_changing_a_role_clears_the_registration_and_extra_roles_and_voids_an_open_sample(admin):
    tag = uuid.uuid4().hex[:8]
    firm, e = f"RoleChange Pty Ltd {tag}", f"rc-{tag}@onboard.invalid"
    run("admin_users.py", "add-firm", "--name", firm)
    run("admin_users.py", "add-user", "--firm", firm, "--email", e, "--role", "approver")
    run("admin_users.py", "register-approver", "--email", e, "--number", "RPEQ 777001", "--register", "RPEQ", "--verified-by", "A. Admin",
        "--evidence", "RPEQ register search 12 Oct 2026: name and number match, current, mechanical")
    run("admin_users.py", "set-role", "--email", e, "--role", "checker")
    row = admin.execute("select u.role::text, u.registration_no, u.also_roles::text, u.id, u.firm_id from app_user u join auth.users a on a.id = u.id"
                        " where a.email = %s", (e,)).fetchone()
    assert row[:3] == ("checker", None, "{}")
    # an open spot-check sample of that person is closed (ledgered) so another checker is not locked out
    rev = uuid.uuid4()
    pid = uuid.uuid4()
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, ncc_edition, approval_date) values (%s, %s, 'x', 'VIC', 6,"
                  " 'NCC2025', '2026-10-01')", (pid, row[4]))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')", (rev, row[4], pid))
    admin.execute("insert into review_sample (firm_id, revision_id, created_by, candidate_ids, sample_ids) values (%s, %s, %s, '{}', '{}')",
                  (row[4], rev, row[3]))
    run("admin_users.py", "set-role", "--email", e, "--role", "approver")
    run("admin_users.py", "set-role", "--email", e, "--role", "checker")
    admin.execute("insert into review_sample (firm_id, revision_id, created_by, candidate_ids, sample_ids) values (%s, %s, %s, '{}', '{}')",
                  (row[4], rev, row[3]))
    run("admin_users.py", "set-role", "--email", e, "--role", "designer")
    assert admin.execute("select count(*) from review_sample where created_by = %s and used_at is null", (row[3],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from ledger_event where firm_id = %s and kind = 'spot_check_sample_voided'", (row[4],)).fetchone()[0] >= 1
