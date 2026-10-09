"""PDF reader: vision output is evidence only. It can never become a SpaceRecord."""
import math
from pathlib import Path
from typing import Any

import pytest
from mep.ingest.pdf import extract_pdf
from reportlab.pdfgen import canvas


class FakeVision:
    """Test double: returns a canned payload and records its calls."""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls: list[tuple[bytes, str]] = []

    def __call__(self, image: bytes, prompt: str) -> Any:
        self.calls.append((image, prompt))
        return self.payload


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    p = tmp_path / "plan.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(100, 700, "Level 1 plan")
    c.showPage()
    c.drawString(100, 700, "Level 2 plan")
    c.save()
    return p


def test_candidates_become_extractions_only(pdf):
    v = FakeVision({"spaces": [
        {"name": "Office", "area_m2": 20.5, "confidence": 0.8, "use": "office", "bogus": 1}]})
    res = extract_pdf(pdf, v)
    assert res.spaces == []
    assert res.source_kind == "pdf"
    assert len(v.calls) == 2  # one per page
    by_field = {e.field: e for e in res.extractions if e.entity_key.startswith("p1-")}
    assert by_field["area_m2"].value == 20.5
    assert by_field["area_m2"].unit == "m^2"
    assert by_field["area_m2"].confidence == 0.8
    assert by_field["name"].value == "Office"
    assert "bogus" not in by_field
    assert all(e.source_kind == "pdf" and e.provenance == "extracted" for e in res.extractions)
    assert all(e.entity_kind == "space" for e in res.extractions)
    assert len(res.source_sha256) == 64


@pytest.mark.parametrize("payload", [
    {"spaces": [{"name": "A", "area_m2": 12}]},
    {"spaces": []},
    {},
    None,
    "garbage",
    {"spaces": "x"},
    {"spaces": [1, None, {"area_m2": float("nan")}]},
])
def test_spaces_always_empty(pdf, payload):
    assert extract_pdf(pdf, FakeVision(payload)).spaces == []


@pytest.mark.parametrize("bad", [-5.0, float("nan"), float("inf"), 0, "12", True])
def test_bad_areas_dropped_with_problem(pdf, bad):
    res = extract_pdf(pdf, FakeVision({"spaces": [{"name": "A", "area_m2": bad}]}))
    assert not [e for e in res.extractions if e.field == "area_m2"]
    assert any("area_m2" in p for p in res.problems)
    assert all(not (isinstance(e.value, float) and math.isnan(e.value)) for e in res.extractions)


def test_bad_confidence_dropped(pdf):
    res = extract_pdf(pdf, FakeVision({"spaces": [{"name": "A", "area_m2": 5, "confidence": 7}]}))
    area = next(e for e in res.extractions if e.field == "area_m2")
    assert area.confidence is None
    assert any("confidence" in p for p in res.problems)


def test_vision_failure_is_a_problem_not_a_crash(pdf):
    def boom(image: bytes, prompt: str) -> Any:
        raise RuntimeError("model down")

    res = extract_pdf(pdf, boom)
    assert res.spaces == [] and res.extractions == []
    assert any("model down" in p for p in res.problems)


def test_prompt_forbids_compliance_judgement(pdf):
    v = FakeVision({"spaces": []})
    extract_pdf(pdf, v)
    assert "compliance" in v.calls[0][1].lower()


def test_module_cannot_construct_space_records():
    import mep.ingest.pdf as mod

    assert not hasattr(mod, "SpaceRecord")
