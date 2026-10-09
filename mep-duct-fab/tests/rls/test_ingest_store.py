"""Ingest results are written as extracted, unconfirmed evidence; a PDF never writes spaces; the engine then refuses."""
from pathlib import Path

import pytest
from mep.ingest.ifc import read_ifc
from mep.ingest.records import ExtractionRecord, IngestResult
from mep.ingest.store import AlreadyIngested, store_ingest

from tests.rls.conftest import uid

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ifc"


@pytest.fixture
def revision(admin, world):
    a = world.firms["A"]
    project, rev = uid(), uid()
    admin.execute("insert into project (id, firm_id, address, state, climate_zone, building_class, ncc_edition)"
                  " values (%s, %s, '3 Test St', 'VIC', 6, '5', 'NCC2025')", (project, a.id))
    admin.execute("insert into revision (id, firm_id, project_id, architect_rev) values (%s, %s, %s, 'A')",
                  (rev, a.id, project))
    return {"firm": a.id, "revision": rev}


def test_an_ifc_is_stored_as_extracted_unconfirmed_spaces_with_evidence_and_health(admin, revision):
    out = store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"],
                       result=read_ifc(FIX / "bsi-arch-ifc4.ifc"))
    assert out["spaces"] == 2 and out["health"]["score_percent"] == 100.0
    rows = admin.execute("select ifc_guid, name, storey, use, area_m2_value, area_m2_unit, area_m2_provenance::text,"
                         " confirmed_by from space where revision_id = %s order by name", (revision["revision"],)).fetchall()
    assert [(r[1], float(r[4]), r[5], r[6], r[7]) for r in rows] == [
        ("entry hall", 6.08, "m^2", "extracted", None), ("living room", 18.495, "m^2", "extracted", None)]
    assert rows[1][0] == "0xY$LvXaDEswJDk_VU74C_" and rows[1][2] == "00 groundfloor" and rows[1][3] == "living area"
    n = admin.execute("select count(*), bool_and(provenance::text = 'extracted') from extraction where revision_id = %s",
                      (revision["revision"],)).fetchone()
    assert n[0] == 2 * 4 and n[1] is True       # name, use, storey, area per space
    run = admin.execute("select source_kind, health->>'score_percent', metadata->>'exporter_profile' from ingest_run"
                        " where revision_id = %s", (revision["revision"],)).fetchone()
    assert run == ("ifc", "100.0", "sketchup")


def test_the_same_file_cannot_be_ingested_twice_into_one_revision(admin, revision):
    res = read_ifc(FIX / "bsi-arch-ifc4.ifc")
    store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"], result=res)
    with pytest.raises(AlreadyIngested):
        store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"], result=res)
    n = admin.execute("select count(*) from space where revision_id = %s", (revision["revision"],)).fetchone()[0]
    assert n == 2          # the failed second ingest left nothing behind


def test_a_pdf_result_writes_evidence_only_and_never_spaces(admin, revision):
    pdf = IngestResult("pdf", "plan.pdf", "d" * 64, extractions=[
        ExtractionRecord("pdf", "plan.pdf", "d" * 64, "space", "p1-0", "area_m2", 41.5, "m^2", confidence=0.6),
        ExtractionRecord("pdf", "plan.pdf", "d" * 64, "space", "p1-0", "name", "Office")])
    out = store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"], result=pdf)
    assert out["spaces"] == 0 and out["extractions"] == 2 and out["health"] is None
    assert admin.execute("select count(*) from space where revision_id = %s", (revision["revision"],)).fetchone()[0] == 0
    assert admin.execute("select count(*) from extraction where revision_id = %s and source_kind = 'pdf'",
                         (revision["revision"],)).fetchone()[0] == 2


def test_a_pdf_result_that_carries_spaces_is_refused(admin, revision):
    from mep.ingest.records import SpaceRecord
    bad = IngestResult("pdf", "plan.pdf", "e" * 64, spaces=[SpaceRecord(key="x", area_m2=10.0, source_kind="pdf")])
    with pytest.raises(ValueError):
        store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"], result=bad)


def test_a_failure_part_way_rolls_everything_back(admin, revision):
    res = read_ifc(FIX / "bsi-arch-ifc4.ifc")
    res.spaces[1] = res.spaces[1].__class__(key="g2", name="x", area_m2=-5.0)       # violates the area check
    with pytest.raises(Exception):  # noqa: B017 - any DB error: the point is the rollback
        store_ingest(admin, firm_id=revision["firm"], revision_id=revision["revision"], result=res)
    for table in ("ingest_run", "extraction", "space"):
        assert admin.execute(f"select count(*) from {table} where revision_id = %s",
                             (revision["revision"],)).fetchone()[0] == 0
