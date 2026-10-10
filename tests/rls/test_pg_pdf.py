"""PDF drawings through the upload door: rendered in the sandbox, read by a (fake) vision model, stored as EVIDENCE only. Needs the local Supabase."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.api.vision_jobs import process_pending
from mep.engine.loader import load_pack
from reportlab.lib.pagesizes import A3
from reportlab.pdfgen import canvas

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL


class FakeVision:
    def __init__(self, payload=None, boom=False):
        self.payload = payload if payload is not None else {"spaces": [
            {"name": "Open office", "area": 108, "use": "Office", "storey": "Level 1", "confidence": 0.8},
            {"name": "Plant", "area": 24, "ceiling_void": 500}]}
        self.boom, self.calls = boom, 0

    def __call__(self, image, prompt):
        self.calls += 1
        if self.boom:
            raise RuntimeError("model unreachable")
        return self.payload


def pdf_bytes(tmp: Path, text="Open office 108 m2", pages=1) -> bytes:
    p = tmp / f"d-{text[:6]}-{pages}.pdf"
    c = canvas.Canvas(str(p), pagesize=A3, invariant=1)
    for i in range(pages):
        c.rect(50, 50, 400, 300)
        c.drawString(100, 200, f"{text} {i}")
        c.showPage()
    c.save()
    return p.read_bytes()


def app(vision):
    client = TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON, vision=vision))
    client.vision = vision                 # the background runner is not started in tests: they run the queued jobs themselves
    return client


def up_raw(client, f, data, name="drawing.pdf", role="designer"):
    """The upload request only: for a PDF it returns at once with a queued job."""
    return client.post(f"/revisions/{f['revision']}/uploads", headers=h.auth(f[role]), files={"file": (name, data)})


def read(client) -> int:
    """Run the queued reading jobs now (what the background runner does)."""
    return process_pending(DB_URL, client.vision)


def up(client, f, data, name="drawing.pdf", role="designer"):
    r = up_raw(client, f, data, name, role)
    if r.status_code == 200:
        read(client)
    return r


def jobs(client, f, role="designer"):
    return client.get(f"/revisions/{f['revision']}/vision-jobs", headers=h.auth(f[role])).json()


def test_a_pdf_becomes_evidence_never_a_space_and_never_an_input(admin, tmp_path):
    vision = FakeVision()
    client, f = app(vision), h.seed(admin)
    r = up_raw(client, f, pdf_bytes(tmp_path))
    assert r.status_code == 200, r.text
    body = r.json()
    # the upload returned at once: nothing was read yet, the job is queued and visible
    assert body["kind"] == "pdf" and body["status"] == "queued" and body["job_id"] and vision.calls == 0
    assert [j["status"] for j in jobs(client, f)] == ["queued"]
    assert admin.execute("select count(*) from extraction where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    assert read(client) == 1 and vision.calls == 1
    done = jobs(client, f)[0]
    assert done["status"] == "done" and done["extractions"] >= 6 and done["error"] is None
    assert admin.execute("select pdf is null from vision_job where id = %s", (body["job_id"],)).fetchone()[0] is True      # the bytes are dropped
    rows = admin.execute("select provenance::text, source_kind, entity_kind from extraction where revision_id = %s", (f["revision"],)).fetchall()
    assert rows and {r[0] for r in rows} == {"extracted"} and {r[1] for r in rows} == {"pdf"} and {r[2] for r in rows} == {"space"}
    assert admin.execute("select count(*) from space where revision_id = %s", (f["revision"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from system_input where firm_id = %s", (f["firm"],)).fetchone()[0] == 0
    assert admin.execute("select storage_path from (select metadata ->> 'storage_path' as storage_path from ingest_run where revision_id = %s) x",
                         (f["revision"],)).fetchone()[0].endswith(".pdf")
    # the engine has nothing to run on: evidence is not an input
    run = client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"]))
    assert run.status_code == 409


def test_the_evidence_table_lists_candidates_with_their_provenance(admin, tmp_path):
    client, f = app(FakeVision()), h.seed(admin)
    up(client, f, pdf_bytes(tmp_path))
    ev = client.get(f"/revisions/{f['revision']}/evidence", headers=h.auth(f["checker"])).json()
    src = ev["sources"][0]
    assert src["kind"] == "pdf" and src["provenance"] == "extracted" and len(src["candidates"]) == 2
    office = next(c for c in src["candidates"] if c["name"] == "Open office")
    assert office["area"] == 108 and office["confidence"] == 0.8 and office["storey"] == "Level 1"
    other = h.seed(admin)
    assert client.get(f"/revisions/{f['revision']}/evidence", headers=h.auth(other["designer"])).status_code == 404
    assert client.get(f"/revisions/{f['revision']}/evidence").status_code == 401


def test_a_pdf_without_a_vision_model_is_still_stored_and_says_nothing_was_read(admin, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client, f = app(None), h.seed(admin)
    r = up(client, f, pdf_bytes(tmp_path, "No model"))
    assert r.status_code == 200 and (j := jobs(client, f)[0])["status"] == "done" and j["extractions"] == 0
    assert any("no vision model is configured" in p for p in j["problems"])


def test_a_failing_model_is_a_problem_not_a_crash_and_junk_output_is_dropped(admin, tmp_path):
    client, f = app(FakeVision(boom=True)), h.seed(admin)
    r = up(client, f, pdf_bytes(tmp_path, "Boom"))
    assert r.status_code == 200 and (j := jobs(client, f)[0])["status"] == "done" and j["extractions"] == 0
    assert any("vision call failed" in p for p in j["problems"])
    client2, f2 = app(FakeVision(payload={"spaces": [{"name": "X", "area": "99999999"}, {"name": "<script>alert(1)</script>", "area": 5}]})), h.seed(admin)
    r2 = up(client2, f2, pdf_bytes(tmp_path, "Junk"))
    assert r2.status_code == 200
    stored = admin.execute("select field, value_text, value_number from extraction where revision_id = %s order by field", (f2["revision"],)).fetchall()
    assert ("name", "<script>alert(1)</script>", None) in stored                        # stored as plain text; React renders it escaped
    assert not any(row[0] == "area" and row[2] == 99999999 for row in stored)


def test_who_may_upload_a_pdf_and_what_is_refused(admin, tmp_path):
    client, f = app(FakeVision()), h.seed(admin)
    data = pdf_bytes(tmp_path, "Roles")
    assert up(client, f, data, role="checker").status_code == 403
    assert up_raw(client, f, b"%PDF-1.7\n" + b"x" * 200).status_code == 200                  # accepted for reading ...
    read(client)
    failed = jobs(client, f)[0]
    assert failed["status"] == "failed" and failed["error"]                                  # ... and the job says why it could not be read
    assert up(client, f, data).status_code == 200
    again = up(client, f, data)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_uploaded"
    big = h.seed(admin)
    huge = b"%PDF-1.4\n" + b"0" * (51 * 1024 * 1024)
    assert up(client, big, huge).status_code == 413


def test_a_pdf_uploaded_to_a_frozen_revision_is_refused_and_makes_no_child(admin, tmp_path):
    client = app(FakeVision())
    f = lin.frozen_parent(admin, client, load_pack(lin.ROOT / "rules"))
    before = admin.execute("select count(*) from revision where project_id = %s", (f["project"],)).fetchone()[0]
    r = up(client, f, pdf_bytes(tmp_path, "Frozen"))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "pdf_on_frozen"
    assert admin.execute("select count(*) from revision where project_id = %s", (f["project"],)).fetchone()[0] == before


@pytest.mark.parametrize("name", ["drawing.pdf", "weird name (1).PDF"])
def test_the_file_name_never_decides_the_type(admin, tmp_path, name):
    client, f = app(FakeVision()), h.seed(admin)
    assert up(client, f, pdf_bytes(tmp_path, f"N{len(name)}"), name=name).json()["kind"] == "pdf"


def test_the_background_runner_takes_the_job_without_the_request_waiting(admin, tmp_path):
    import time

    from mep.api.vision_jobs import VisionRunner
    vision = FakeVision()
    client, f = app(vision), h.seed(admin)
    runner = VisionRunner(DB_URL, vision, poll=0.2)
    runner.start()
    try:
        r = up_raw(client, f, pdf_bytes(tmp_path, "Background"))
        assert r.status_code == 200 and r.json()["status"] == "queued"
        states = []
        for _ in range(100):
            states.append(jobs(client, f)[0]["status"])
            if states[-1] in ("done", "failed"):
                break
            time.sleep(0.2)
        assert states[-1] == "done" and vision.calls == 1
    finally:
        runner.stop()


def test_another_firm_cannot_see_the_job_and_a_client_cannot_write_or_read_the_bytes(admin, tmp_path):
    client, f = app(FakeVision()), h.seed(admin)
    other = h.seed(admin)
    assert up_raw(client, f, pdf_bytes(tmp_path, "Mine")).status_code == 200
    assert client.get(f"/revisions/{f['revision']}/vision-jobs", headers=h.auth(other["designer"])).status_code == 404
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege), psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("select pdf from vision_job")
    with pytest.raises(psycopg.errors.InsufficientPrivilege), psycopg.connect(DB_URL, autocommit=True) as c:
        c.execute("set role authenticated")
        c.execute("update vision_job set status = 'done'")


def test_a_job_left_running_by_a_stopped_server_is_taken_again_then_failed_so_the_file_can_be_uploaded_again(admin, tmp_path):
    from mep.api.vision_jobs import housekeeping
    vision = FakeVision()
    client, f = app(vision), h.seed(admin)
    body = up_raw(client, f, pdf_bytes(tmp_path, "Stale")).json()
    admin.execute("update vision_job set status = 'running', attempts = 1, started_at = now() - interval '1 hour' where id = %s", (body["job_id"],))
    housekeeping(DB_URL)
    assert jobs(client, f)[0]["status"] == "queued"                                       # taken again
    admin.execute("update vision_job set status = 'running', attempts = 3, started_at = now() - interval '1 hour' where id = %s", (body["job_id"],))
    housekeeping(DB_URL)
    assert jobs(client, f)[0]["status"] == "failed"
    assert up_raw(client, f, pdf_bytes(tmp_path, "Stale")).status_code == 200            # the failed job does not block a new upload


def test_a_busy_reader_pool_delays_the_job_without_using_up_its_attempts(admin, tmp_path, monkeypatch):
    from mep.api import vision_jobs
    from mep.api.uploads import UploadRefused
    vision = FakeVision()
    client, f = app(vision), h.seed(admin)
    body = up_raw(client, f, pdf_bytes(tmp_path, "Busy")).json()

    def busy(*a, **k):
        raise UploadRefused(503, "busy", "several files are being read right now")
    monkeypatch.setattr(vision_jobs, "read_pdf", busy)
    read(client)
    row = admin.execute("select status, attempts, not_before > now() from vision_job where id = %s", (body["job_id"],)).fetchone()
    assert row == ("queued", 0, True)
    read(client)                                                                          # not taken again until its delay passes
    assert admin.execute("select status, attempts from vision_job where id = %s", (body["job_id"],)).fetchone() == ("queued", 0)
