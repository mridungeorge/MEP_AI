"""PDF drawings through the upload door: rendered in the sandbox, read by a (fake) vision model, stored as EVIDENCE only. Needs the local Supabase."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mep.api.server import create_pg_app
from mep.engine.loader import load_pack
from reportlab.lib.pagesizes import A3
from reportlab.pdfgen import canvas

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.conftest import DB_URL


class FakeVision:
    def __init__(self, payload=None, boom=False):
        self.payload = payload if payload is not None else {"spaces": [
            {"name": "Open office", "area_m2": 108, "use": "Office", "storey": "Level 1", "confidence": 0.8},
            {"name": "Plant", "area_m2": 24, "ceiling_void_mm": 500}]}
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
    return TestClient(create_pg_app(DB_URL, h.SECRET, load_pack(lin.ROOT / "rules"), supabase_url=lin.SUPABASE_URL, anon_key=lin.ANON, vision=vision))


def up(client, f, data, name="drawing.pdf", role="designer"):
    return client.post(f"/revisions/{f['revision']}/uploads", headers=h.auth(f[role]), files={"file": (name, data)})


def test_a_pdf_becomes_evidence_never_a_space_and_never_an_input(admin, tmp_path):
    vision = FakeVision()
    client, f = app(vision), h.seed(admin)
    r = up(client, f, pdf_bytes(tmp_path))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "pdf" and body["spaces"] == 0 and body["extractions"] >= 6 and body["health"] is None and vision.calls == 1
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
    assert office["area_m2"] == 108 and office["confidence"] == 0.8 and office["storey"] == "Level 1"
    other = h.seed(admin)
    assert client.get(f"/revisions/{f['revision']}/evidence", headers=h.auth(other["designer"])).status_code == 404
    assert client.get(f"/revisions/{f['revision']}/evidence").status_code == 401


def test_a_pdf_without_a_vision_model_is_still_stored_and_says_nothing_was_read(admin, tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client, f = app(None), h.seed(admin)
    r = up(client, f, pdf_bytes(tmp_path, "No model"))
    assert r.status_code == 200 and r.json()["extractions"] == 0 and any("no vision model is configured" in p for p in r.json()["problems"])


def test_a_failing_model_is_a_problem_not_a_crash_and_junk_output_is_dropped(admin, tmp_path):
    client, f = app(FakeVision(boom=True)), h.seed(admin)
    r = up(client, f, pdf_bytes(tmp_path, "Boom"))
    assert r.status_code == 200 and r.json()["extractions"] == 0 and any("vision call failed" in p for p in r.json()["problems"])
    client2, f2 = app(FakeVision(payload={"spaces": [{"name": "X", "area_m2": "99999999"}, {"name": "<script>alert(1)</script>", "area_m2": 5}]})), h.seed(admin)
    r2 = up(client2, f2, pdf_bytes(tmp_path, "Junk"))
    assert r2.status_code == 200
    stored = admin.execute("select field, value_text, value_number from extraction where revision_id = %s order by field", (f2["revision"],)).fetchall()
    assert ("name", "<script>alert(1)</script>", None) in stored                        # stored as plain text; React renders it escaped
    assert not any(row[0] == "area_m2" and row[2] == 99999999 for row in stored)


def test_who_may_upload_a_pdf_and_what_is_refused(admin, tmp_path):
    client, f = app(FakeVision()), h.seed(admin)
    data = pdf_bytes(tmp_path, "Roles")
    assert up(client, f, data, role="checker").status_code == 403
    assert up(client, f, b"%PDF-1.7\n" + b"x" * 200).status_code == 422                       # not a real PDF
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
