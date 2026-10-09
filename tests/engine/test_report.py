import copy
import io
import json
from datetime import date
from pathlib import Path

import pypdf
from mep.engine.loader import load_pack
from mep.engine.model import InputValue, ProjectFacts, Provenance, RunRequest, Subject
from mep.engine.report import DRAFT_BANNER, to_json, to_pdf
from mep.engine.runner import run

ROOT = Path(__file__).resolve().parents[2]
PACK = load_pack(ROOT / "rules")
ECON25 = "NCC2025-J6D3-econ-cycle"


def iv(v, u=None):
    return InputValue(v, u, Provenance.ENGINEER_CONFIRMED)


def request(n_subjects=1):
    project = ProjectFacts("VIC", "NCC2025", 6, "5", date(2026, 10, 6))
    inputs = {"max_airside_component_airflow": iv(1500, "L/s"), "economy_cycle": iv(False),
              "system_type": iv("air_conditioning"), "provides_required_mech_ventilation": iv(True),
              "dehumidification_control_needed": iv(False), "is_electricity_substation": iv(False)}
    return RunRequest(project, [Subject(f"ahu-{i}", [ECON25], dict(inputs)) for i in range(n_subjects)])


def test_banner_text_is_exact():
    assert DRAFT_BANNER == "DRAFT RULES: NOT ENGINEER-APPROVED"


def test_json_report_carries_the_banner_while_any_rule_is_draft():
    report = run(request(), PACK)
    assert report["banner"] == DRAFT_BANNER
    assert report["draft_rules"] == [ECON25]
    parsed = json.loads(to_json(report))
    assert parsed["banner"] == DRAFT_BANNER
    assert next(iter(parsed)) == "banner"           # first thing a reader sees
    assert parsed["summary"]["FAIL"] == 1


def test_banner_disappears_only_when_every_rule_used_is_approved():
    approved = copy.deepcopy(PACK)
    approved.rules[ECON25].raw["status"] = "approved"
    approved.rules[ECON25].raw["reviewed_by"] = "test engineer"
    approved.rules[ECON25].raw["reviewed_on"] = "2026-10-06"
    report = run(request(), approved)
    assert report["banner"] is None and report["draft_rules"] == []
    mixed = run(request(), PACK)
    assert mixed["banner"] == DRAFT_BANNER


def test_pdf_has_the_banner_on_every_page(tmp_path):
    report = run(request(n_subjects=60), PACK)
    out = tmp_path / "report.pdf"
    to_pdf(report, out)
    pages = pypdf.PdfReader(io.BytesIO(out.read_bytes())).pages
    assert len(pages) > 1
    for page in pages:
        assert DRAFT_BANNER in page.extract_text()


def test_pdf_lists_each_result_with_its_clause_and_outcome(tmp_path):
    report = run(request(), PACK)
    out = tmp_path / "r.pdf"
    to_pdf(report, out)
    text = "\n".join(p.extract_text() for p in pypdf.PdfReader(str(out)).pages)
    flat = "".join(text.split())  # table cells wrap: compare without whitespace
    clause = report["results"][0]["citation"]["clause"]
    assert ECON25 in flat and "FAIL" in flat and "".join(clause.split()) in flat
    assert "Hypothesis:verify" in flat


def test_report_never_contains_standards_wording_keys():
    report = run(request(), PACK)
    assert "clause_text" not in json.dumps(report)
