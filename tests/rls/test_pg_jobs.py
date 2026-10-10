"""The drafting job queue end to end: the API enqueues, the dispatcher runs the job (here with the local executor; the container executor is
covered in tests/skills/test_worker_isolation.py) and the API gets back a result it re-verifies. Needs the local Supabase."""
import json
import threading
from pathlib import Path

import pytest
from mep.api.schedule import CurrentUser
from mep.api.skills_pg import PgSkills
from mep.skills_runner import jobs
from mep.skills_runner.dispatcher import serve
from mep.skills_runner.jobs import enqueue_and_wait, result_from_json, result_to_json
from mep.skills_runner.runner import LocalExecutor, SkillUnavailable, run_skill

from tests.rls import test_pg_api as h
from tests.rls.conftest import DB_URL

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "skills/space-envelope/examples/office_floor/spec.json").read_text(encoding="utf-8"))


@pytest.fixture
def worker():
    stop = threading.Event()
    t = threading.Thread(target=serve, args=(DB_URL, LocalExecutor()), kwargs={"poll": 0.1, "stop": stop}, daemon=True)
    t.start()
    yield
    stop.set()
    t.join(timeout=30)


def user_of(f):
    return CurrentUser(user_id=f["designer"], firm_id=f["firm"], role="designer")


def test_a_job_goes_through_the_queue_and_comes_back_released_and_intact(admin, worker):
    f = h.seed(admin)
    result = enqueue_and_wait(DB_URL, firm_id=f["firm"], revision_id=f["revision"], user_id=f["designer"], skill="space-envelope", spec=SPEC)
    assert result.status == "ok" and result.released and {x.role for x in result.files} >= {"ifc", "dxf", "manifest"}
    assert all(x.content is not None for x in result.files)
    local = run_skill("space-envelope", SPEC)
    assert [(x.name, x.sha256) for x in result.files] == [(x.name, x.sha256) for x in local.files]       # the same bytes as a local run
    assert admin.execute("select count(*) from skill_job where firm_id = %s", (f["firm"],)).fetchone()[0] == 0     # cleaned up


def test_a_rejected_spec_comes_back_with_no_content(admin, worker):
    f = h.seed(admin)
    bad = {**SPEC, "rooms": [{**SPEC["rooms"][0], "height_mm": 99999}]}
    result = enqueue_and_wait(DB_URL, firm_id=f["firm"], revision_id=f["revision"], user_id=f["designer"], skill="space-envelope", spec=bad)
    assert result.status == "spec_rejected" and not result.released and all(x.content is None for x in result.files)


def test_without_a_worker_the_api_says_so_instead_of_hanging(admin):
    f = h.seed(admin)
    with pytest.raises(SkillUnavailable, match="no drafting worker"):
        enqueue_and_wait(DB_URL, firm_id=f["firm"], revision_id=f["revision"], user_id=f["designer"], skill="space-envelope", spec=SPEC,
                         pickup_seconds=1.0, wait_seconds=3)
    assert admin.execute("select count(*) from skill_job where firm_id = %s", (f["firm"],)).fetchone()[0] == 0


def test_a_tampered_result_is_not_released(admin, worker):
    f = h.seed(admin)
    ok = run_skill("space-envelope", SPEC)
    meta, contents = result_to_json(ok)
    bad = dict(contents)
    first = next(iter(bad))
    bad[first] = bad[first] + b"\n"                                       # a byte changed in transit
    got = result_from_json(meta, bad)
    assert got.status == "revalidation_failed" and all(x.content is None for x in got.files)
    missing = {k: v for k, v in contents.items() if k != first}
    assert result_from_json(meta, missing).status == "revalidation_failed"
    swapped = json.loads(json.dumps(meta))
    swapped["manifest"]["files"][0]["sha256"] = "0" * 64
    assert result_from_json(swapped, contents).status == "revalidation_failed"
    assert f


def test_the_service_in_queue_mode_runs_through_the_worker_and_clients_cannot_read_the_queue(admin, worker, monkeypatch):
    monkeypatch.setenv("MEP_SKILL_EXECUTOR", "queue")
    f = h.seed(admin)
    svc = PgSkills(DB_URL, user_of(f))
    result = svc.execute(f["revision"], "space-envelope", SPEC)
    assert result.released
    from mep.skills_runner.registry import availability
    assert availability("duct-fab") == (True, "")                              # the packages are the worker's concern in queue mode
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select * from skill_job")
    assert jobs.WAIT_SECONDS > 300
