"""The staging smoke script, run against a live uvicorn of this API on the local database: it must pass on a healthy deployment and fail loudly on a
broken one. Needs the local Supabase."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mep.api.auth import mint_token
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def live_api():
    port = free_port()
    env = {**os.environ, "MEP_DB_URL": DB_URL, "MEP_JWT_SECRET": h.SECRET, "MEP_ALLOW_DEMO_JWT_SECRET": "1", "MEP_SUPABASE_URL": lin.SUPABASE_URL,
           "MEP_SUPABASE_ANON_KEY": lin.ANON, "MEP_COOKIE_SECURE": "0", "MEP_ALLOW_LOCAL_SKILLS": "1", "MEP_VISION_WORKER": "0", "MEP_RULES_DIR": str(ROOT / "rules")}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--factory", "mep.api.server:app_from_env", "--port", str(port), "--log-level", "warning"],
                            env=env, cwd=ROOT)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("the API did not start")
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=20)


def smoke(url, token, revision, *extra, confirmed=True):
    flag = ["--i-confirm-this-revision-is-throwaway", str(revision)] if confirmed else []
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "staging_smoke.py"), "--api", url, "--revision", str(revision),
                           "--designer-token", token, *flag, *extra], capture_output=True, text=True, timeout=300, check=False)


def prepared(admin):
    from fastapi.testclient import TestClient
    from mep.api.server import create_pg_app
    pack = load_pack(ROOT / "rules")
    client = TestClient(create_pg_app(DB_URL, h.SECRET, pack, supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON))
    f = h.seed(admin)
    h.populate(client, pack, f)
    for sp in client.get(f"{h.base(f)}/gate1", headers=h.auth(f["designer"])).json()["spaces"]:
        admin.execute("delete from space where id = %s", (sp["id"],))
    return f


def test_the_smoke_test_covers_sign_in_upload_gate1_run_and_report_with_a_designer_token(admin, live_api):
    f = prepared(admin)
    token = mint_token(h.SECRET, f["designer"], ttl_seconds=600)
    r = smoke(live_api, token, f["revision"])
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("health check", "sign in", "upload the fixture IFC", "Gate 1", "run the rules", "report"):
        assert any(line.startswith("PASS") and step in line for line in r.stdout.splitlines()), (step, r.stdout)
    assert "SKIP" in r.stdout and "SMOKE TEST PASSED" in r.stdout and "FAIL" not in r.stdout
    again = smoke(live_api, token, f["revision"])                           # results now exist: the same revision cannot be confirmed again
    assert again.returncode == 1 and "FAIL" in again.stdout


def test_the_smoke_test_goes_on_to_sign_off_and_a_working_share_link_with_all_three_tokens(admin, live_api):
    from tests.rls.test_pg_review_api import with_approver
    f = with_approver(admin, prepared(admin), registration="RPEQ 54321")
    tokens = {role: mint_token(h.SECRET, f[role], ttl_seconds=600) for role in ("designer", "checker", "approver")}
    r = smoke(live_api, tokens["designer"], f["revision"], "--checker-token", tokens["checker"], "--approver-token", tokens["approver"],
              "--registration", "RPEQ 54321")
    assert r.returncode == 0, r.stdout + r.stderr
    for step in ("freeze the revision", "Gate 2", "Gate 3", "create a share link", "exchange the link", "reads the package", "revoking the link"):
        assert any(line.startswith("PASS") and step in line for line in r.stdout.splitlines()), (step, r.stdout)
    assert "FAIL" not in r.stdout
    wrong = smoke(live_api, tokens["designer"], prepared(admin)["revision"], "--checker-token", tokens["checker"], "--approver-token", tokens["approver"])
    assert wrong.returncode == 1                                              # a revision of another firm: nothing passes


def test_the_smoke_test_fails_loudly_with_a_bad_token_a_bad_revision_or_no_server(admin, live_api):
    f = prepared(admin)
    bad = smoke(live_api, "not-a-token", f["revision"])
    assert bad.returncode == 1 and "FAIL" in bad.stdout and "SMOKE TEST FAILED" in bad.stdout
    token = mint_token(h.SECRET, f["designer"], ttl_seconds=600)
    other = smoke(live_api, token, "00000000-0000-0000-0000-000000000000")
    assert other.returncode == 1 and "FAIL" in other.stdout
    down = smoke("http://127.0.0.1:9", token, f["revision"])
    assert down.returncode == 1 and "FAIL" in down.stdout
    assert smoke(live_api, "", f["revision"]).returncode == 64


def test_the_smoke_test_refuses_a_revision_that_is_not_marked_throwaway(admin, live_api):
    f = prepared(admin)
    token = mint_token(h.SECRET, f["designer"], ttl_seconds=600)
    r = smoke(live_api, token, f["revision"], confirmed=False)
    assert r.returncode == 1 and "refused" in r.stdout and "Gate 1" not in r.stdout
    assert admin.execute("select count(*) from ingest_run where revision_id = %s", (f["revision"],)).fetchone()[0] == 0       # nothing was touched
    admin.execute("update project set address = 'SMOKE TEST (throwaway, synthetic)' where id = (select project_id from revision where id = %s)", (f["revision"],))
    assert smoke(live_api, token, f["revision"], confirmed=False).returncode == 0                                        # the marked project is accepted
