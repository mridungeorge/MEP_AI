"""A space made from a drawing reading stays EXTRACTED: linked to its evidence and page, unit declared (never assumed), needs Gate 1, and cannot be
edited or relabelled as hand-entered. Needs the local Supabase."""
import psycopg
import pytest
from mep.engine.loader import load_pack

from tests.rls import test_pg_api as h
from tests.rls import test_pg_lineage as lin
from tests.rls.test_pg_pdf import FakeVision, app, pdf_bytes, up

PACK = load_pack(lin.ROOT / "rules")


def evidence_ready(admin, tmp_path, payload=None):
    client, f = app(FakeVision(payload)), h.seed(admin)
    h.populate(client, PACK, f)
    assert up(client, f, pdf_bytes(tmp_path, "Evidence")).status_code == 200
    src = client.get(f"/revisions/{f['revision']}/evidence", headers=h.auth(f["designer"])).json()["sources"][0]
    return client, f, src


def add(client, f, src, key="p1-1", **kw):
    body = {"source_sha256": src["sha256"], "entity_key": key, "area_unit": "m^2", **kw}
    return client.post(f"/revisions/{f['revision']}/evidence/spaces", headers=h.auth(f.get("as", f["designer"])), json=body)


def test_a_unit_must_be_stated_and_only_a_designer_adds(admin, tmp_path):
    client, f, src = evidence_ready(admin, tmp_path)
    no_unit = client.post(f"/revisions/{f['revision']}/evidence/spaces", headers=h.auth(f["designer"]),
                          json={"source_sha256": src["sha256"], "entity_key": "p1-1"})
    assert no_unit.status_code == 422
    assert add(client, f, src, area_unit="acres").status_code == 422
    assert add(client, f, src, key="p1-2").status_code == 422                       # that candidate has a void but no unit given for it
    assert client.post(f"/revisions/{f['revision']}/evidence/spaces", headers=h.auth(f["checker"]),
                       json={"source_sha256": src["sha256"], "entity_key": "p1-1", "area_unit": "m^2"}).status_code == 403
    assert admin.execute("select count(*) from space where revision_id = %s and evidence_area_id is not null", (f["revision"],)).fetchone()[0] == 0


def test_the_space_stays_extracted_linked_and_converted_from_the_declared_unit(admin, tmp_path):
    client, f, src = evidence_ready(admin, tmp_path)
    r = add(client, f, src, area_unit="ft^2")
    assert r.status_code == 200 and r.json()["provenance"] == "extracted"
    row = admin.execute("select name, area_m2_value, area_m2_provenance::text, evidence_page, evidence_area_unit, confirmed_by,"
                        " (select entity_key || '/' || field || '/' || source_sha256 from extraction e where e.id = evidence_area_id)"
                        " from space where id = %s", (r.json()["space_id"],)).fetchone()
    assert row[0] == "Open office" and float(row[1]) == pytest.approx(108 * 0.09290304, abs=1e-5)
    assert row[2] == "extracted" and row[3] == 1 and row[4] == "ft^2" and row[5] is None
    assert row[6] == f"p1-1/area/{src['sha256']}"
    v = client.get(f"/revisions/{f['revision']}/gate1", headers=h.auth(f["designer"])).json()
    s = next(x for x in v["spaces"] if x["name"] == "Open office")
    assert s["provenance"] == "extracted" and s["manual_trace"] is False and s["evidence"]["page"] == 1 and s["evidence"]["area_unit"] == "ft^2"
    assert add(client, f, src, area_unit="m^2").status_code == 409                   # once only
    assert admin.execute("select count(*) from ledger_event where revision_id = %s and kind = 'evidence_space_added'", (f["revision"],)).fetchone()[0] == 1


def test_an_evidence_space_cannot_reach_the_engine_unconfirmed(admin, tmp_path):
    client, f, src = evidence_ready(admin, tmp_path)
    assert add(client, f, src).status_code == 200
    rows = h.rows_to_confirm(client, f)
    evidence_rows = [r for r in rows if r["kind"] == "space" and r["id"] in {
        str(x[0]) for x in admin.execute("select id from space where evidence_area_id is not null and revision_id = %s", (f["revision"],)).fetchall()}]
    assert len(evidence_rows) == 1
    assert h.confirm(client, f, [r for r in rows if r not in evidence_rows]).status_code == 200
    run = client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"]))
    assert run.status_code == 409 and run.json()["code"] in ("gate1_required", "extracted_inputs")
    assert h.confirm(client, f, evidence_rows).status_code == 200
    assert admin.execute("select area_m2_provenance::text from space where evidence_area_id is not null and revision_id = %s", (f["revision"],)).fetchone()[0] == "engineer_confirmed"
    assert client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"])).status_code == 200


def test_the_area_cannot_be_edited_or_relabelled_as_hand_entered(admin, tmp_path):
    client, f, src = evidence_ready(admin, tmp_path)
    sid = add(client, f, src).json()["space_id"]
    base = f"/revisions/{f['revision']}/gate1/spaces/{sid}"
    assert client.put(base, headers=h.auth(f["designer"]), json={"area_m2": 500}).status_code == 409
    renamed = client.put(base, headers=h.auth(f["designer"]), json={"name": "Open office (east)"})
    assert renamed.status_code == 200 and renamed.json()["provenance"] == "extracted"
    attempts = [
        "update space set area_m2_provenance = 'default' where id = %s",
        "update space set area_m2_provenance = 'calculated' where id = %s",
        "update space set area_m2_value = 1 where id = %s",
        "update space set evidence_area_id = null where id = %s",
        "update space set evidence_page = 9 where id = %s",
        "update space set evidence_area_unit = 'ft^2' where id = %s",
    ]
    for sql in attempts:
        with pytest.raises(psycopg.errors.RaiseException):
            admin.execute(sql, (sid,))
    ex = admin.execute("select evidence_area_id from space where id = %s", (sid,)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException):          # a forged link: provenance not extracted, or a row of another revision
        admin.execute("insert into space (firm_id, revision_id, name, area_m2_value, area_m2_provenance, evidence_area_id)"
                      " values (%s, %s, 'forged', 5, 'default', %s)", (f["firm"], f["revision"], ex))
    other = h.seed(admin)
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("insert into space (firm_id, revision_id, name, area_m2_value, area_m2_provenance, evidence_area_id)"
                      " values (%s, %s, 'forged', 5, 'extracted', %s)", (other["firm"], other["revision"], ex))


def test_confirming_and_then_renaming_withdraws_the_confirmation_but_keeps_the_provenance(admin, tmp_path):
    client, f, src = evidence_ready(admin, tmp_path)
    sid = add(client, f, src).json()["space_id"]
    rows = [r for r in h.rows_to_confirm(client, f) if r["id"] == sid]
    assert h.confirm(client, f, rows).status_code == 200
    base = f"/revisions/{f['revision']}/gate1/spaces/{sid}"
    assert client.put(base, headers=h.auth(f["designer"]), json={"name": "Renamed"}).status_code == 200
    raw = admin.execute("select area_m2_provenance::text, confirmed_by from space where id = %s", (sid,)).fetchone()
    assert raw[0] == "engineer_confirmed" and raw[1] is None            # the engine still refuses it: nobody confirmed this version
    assert client.post(f"/revisions/{f['revision']}/run-rules", headers=h.auth(f["designer"])).status_code == 409
