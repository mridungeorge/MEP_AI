"""Health score: a percentage plus a fix list; below the threshold the file is not good enough to proceed on."""
from pathlib import Path

import pytest
import yaml
from mep.ingest.health import HealthReport, load_policy, score
from mep.ingest.ifc import read_ifc
from mep.ingest.records import IngestResult, SpaceRecord

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "ifc"


def ifc_result(**meta):
    base = {"schema": "IFC4", "schema_supported": True, "exporter_profile": "revit", "profile_status": "unverified",
            "area_source": "quantities", "spaces_total": 10, "spaces_with_area": 10, "spaces_without_storey": 0,
            "unnamed_spaces": 0, "duplicate_spaces": 0}
    base.update(meta)
    spaces = [SpaceRecord(key=f"g{i}", name=f"R{i}", area_m2=20.0, storey="L1") for i in range(base["spaces_total"])]
    return IngestResult("ifc", "m.ifc", "0" * 64, spaces=spaces, metadata=base)


def dxf_result(**meta):
    base = {"units": "mm", "scale_ok": True, "scale_factor_to_m": 0.001, "scale_check": "ok", "extents_m": [30.0, 20.0],
            "layer_names": ["A-ROOM"], "space_layers": ["A-ROOM"], "closed_polylines": 10, "open_polylines": 0,
            "duplicate_polylines": 0, "self_intersecting": 0, "curved_polylines": 0, "text_labels": 10,
            "labelled_spaces": 10}
    base.update(meta)
    spaces = [SpaceRecord(key=f"h{i}", name=f"R{i}", area_m2=20.0, source_kind="dxf") for i in range(base["closed_polylines"])]
    return IngestResult("dxf", "p.dxf", "0" * 64, spaces=spaces, metadata=base)


def test_the_policy_weights_sum_to_100_and_has_a_threshold():
    policy = load_policy()
    assert sum(policy["weights"].values()) == 100 and 0 < policy["minimum_percent"] < 100
    assert set(policy["weights"]) == {"closed_boundaries", "scale", "layers", "duplicates", "names", "schema", "exporter"}


def test_a_clean_ifc_from_a_verified_exporter_scores_100_and_has_no_fixes():
    r = score(ifc_result(exporter_profile="sketchup", profile_status="verified"))
    assert isinstance(r, HealthReport) and r.score_percent == 100.0 and r.fixes == [] and not r.below_threshold


def test_an_unverified_exporter_costs_points_and_says_why():
    r = score(ifc_result())
    assert 90 <= r.score_percent < 100
    assert any("exporter" in f.lower() and "revit" in f.lower() for f in r.fixes)


def test_unknown_exporter_costs_more():
    assert score(ifc_result(exporter_profile="unknown")).score_percent < score(ifc_result()).score_percent


def test_every_failing_check_adds_a_specific_fix_with_counts():
    r = score(ifc_result(spaces_with_area=6, unnamed_spaces=3, duplicate_spaces=2, spaces_without_storey=4,
                         schema_supported=False))
    text = " | ".join(r.fixes)
    assert "4 of 10" in text and "3 of 10" in text and "2 of 10" in text and "storey" in text.lower()
    assert "schema" in text.lower()
    assert r.score_percent < 80


def test_below_the_threshold_the_report_recommends_a_clean_file():
    r = score(ifc_result(spaces_with_area=1, unnamed_spaces=8, duplicate_spaces=5, schema_supported=False,
                         exporter_profile="unknown", spaces_without_storey=10))
    assert r.below_threshold and "request a clean IFC" in r.recommendation


def test_a_clean_dxf_scores_high_and_an_unknown_scale_scores_low():
    assert score(dxf_result()).score_percent >= 85
    unscaled = dxf_result(units="unitless", scale_ok=False, scale_factor_to_m=None, scale_check="unknown")
    unscaled.spaces.clear()          # the reader emits no areas when the scale is unknown
    bad = score(unscaled)
    assert bad.below_threshold and any("scale" in f.lower() for f in bad.fixes)


def test_open_polylines_and_duplicates_in_a_dxf_are_reported():
    r = score(dxf_result(closed_polylines=6, open_polylines=4, duplicate_polylines=2, labelled_spaces=3))
    text = " | ".join(r.fixes).lower()
    assert "open" in text and "duplicate" in text and "label" in text


def test_a_dxf_with_no_space_layer_is_flagged_on_layers():
    r = score(dxf_result(space_layers=[], closed_polylines=0))
    assert any("layer" in f.lower() for f in r.fixes) and r.below_threshold


def test_a_file_with_no_spaces_scores_zero_on_boundaries_and_recommends_a_clean_file():
    r = score(ifc_result(spaces_total=0, spaces_with_area=0))
    assert r.below_threshold and r.checks["closed_boundaries"].fraction == 0.0


def test_pdf_and_excel_extractions_are_never_scored_as_a_clean_model():
    r = score(IngestResult("pdf", "p.pdf", "0" * 64))
    assert r.score_percent == 0.0 and r.below_threshold and "vision" in r.recommendation.lower()


def test_the_real_public_fixture_scores_as_measured_and_reports_its_checks():
    r = score(read_ifc(FIX / "bsi-arch-ifc4.ifc"))
    assert r.score_percent == 100.0 and r.checks["exporter"].fraction == 1.0
    assert set(r.checks) == set(load_policy()["weights"])


def test_fractions_are_bounded_even_for_inconsistent_metadata():
    r = score(ifc_result(spaces_total=2, spaces_with_area=9, unnamed_spaces=7, duplicate_spaces=9))
    assert all(0.0 <= c.fraction <= 1.0 for c in r.checks.values()) and 0.0 <= r.score_percent <= 100.0


def test_the_threshold_comes_from_the_yaml_policy(tmp_path):
    policy = yaml.safe_load((ROOT / "apps/api/mep/ingest/health.yaml").read_text(encoding="utf-8"))
    policy["minimum_percent"] = 99
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump(policy), encoding="utf-8")
    assert score(ifc_result(), policy=load_policy(f)).below_threshold
    assert not score(ifc_result()).below_threshold


@pytest.mark.parametrize("bad", [{"weights": {"scale": 50}, "minimum_percent": 70},
                                 {"weights": {}, "minimum_percent": 70}, "x", None])
def test_a_malformed_policy_is_refused(tmp_path, bad):
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(f)
