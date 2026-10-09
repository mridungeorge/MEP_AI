"""The release gate: no release claim while there are zero REAL (non-synthetic) golden projects."""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import release_gate as rg

REAL = {"synthetic": False, "firm": "Pilot Mechanical Pty Ltd", "data_agreement": "DA-2026-01",
        "engineer_signed_off_by": "E. Engineer RPEQ 12345"}


def make(golden, name, agreement=True, **meta):
    d = golden / name
    d.mkdir(parents=True)
    (d / "project.yaml").write_text("description: x\n", encoding="utf-8")
    (d / "meta.yaml").write_text(yaml.safe_dump({"name": name, **meta}), encoding="utf-8")
    if agreement:
        (d / "data_agreement.pdf").write_bytes(b"%PDF-1.4 signed agreement")
    return d


@pytest.fixture
def golden(tmp_path, monkeypatch):
    g = tmp_path / "golden"
    g.mkdir()
    monkeypatch.setattr(rg, "GOLDEN", g)
    return g


def test_the_six_repo_projects_are_all_synthetic_and_there_are_no_real_ones():
    synthetic, real = rg.counts()
    assert synthetic >= 6 and real == 0
    assert rg.check_labels() == []


def test_check_blocks_while_there_are_zero_real_projects(golden, capsys):
    make(golden, "syn-a", agreement=False, synthetic=True)
    assert rg.main(["--check"]) == 1
    assert "BLOCKED" in capsys.readouterr().err


def test_check_passes_once_a_real_project_is_fully_documented(golden):
    make(golden, "syn-a", agreement=False, synthetic=True)
    make(golden, "pilot-1", **REAL)
    assert rg.counts() == (1, 1)
    assert rg.main(["--check"]) == 0


# ---- a project cannot be made to look real ---------------------------------------------------------

@pytest.mark.parametrize("placeholder", ["TBD", "N/A", "-", "x", "null", "none", "test", "TODO", "unknown", "fake",
                                         chr(0x200B) * 6, "   ", "123456", "true", ""])
@pytest.mark.parametrize("field", ["firm", "data_agreement", "engineer_signed_off_by"])
def test_placeholder_text_in_any_real_field_does_not_count(golden, field, placeholder):
    make(golden, "pilot-1", **{**REAL, field: placeholder})
    assert rg.counts() == (1, 0)


def test_non_text_values_do_not_count(golden):
    for i, value in enumerate([True, 5, ["Firm Ltd"], {"a": "Firm Ltd"}]):
        make(golden, f"pilot-{i}", **{**REAL, "firm": value})
    assert rg.counts()[1] == 0


def test_the_signing_engineer_needs_a_registration_number(golden):
    make(golden, "pilot-1", **{**REAL, "engineer_signed_off_by": "Jane Engineer"})
    assert rg.counts() == (1, 0)


def test_the_agreement_must_exist_as_a_non_empty_file(golden):
    make(golden, "pilot-1", agreement=False, **REAL)
    assert rg.counts() == (1, 0)
    (golden / "pilot-1" / "data_agreement.pdf").write_bytes(b"")
    assert rg.counts() == (1, 0)
    (golden / "pilot-1" / "data_agreement.pdf").write_bytes(b"signed")
    assert rg.counts() == (0, 1)


def test_a_real_project_cannot_wear_the_synthetic_prefix_or_a_wrong_name(golden):
    make(golden, "syn-fake", **REAL)
    d = make(golden, "pilot-2", **REAL)
    (d / "meta.yaml").write_text(yaml.safe_dump({**REAL, "name": "someone-else"}), encoding="utf-8")
    assert rg.counts() == (2, 0)


def test_the_fake_directory_from_the_review_does_not_unblock_anything(golden, capsys):
    make(golden, "fake", agreement=False, synthetic=False, firm="TBD", data_agreement="TBD", engineer_signed_off_by="TBD")
    assert rg.counts() == (1, 0)
    assert rg.main(["--check"]) == 1
    assert rg.check_labels()


@pytest.mark.parametrize("missing", ["firm", "data_agreement", "engineer_signed_off_by"])
def test_a_project_is_not_real_unless_all_three_fields_are_filled_in(golden, missing):
    make(golden, "pilot-1", **{**REAL, missing: None})
    assert rg.counts() == (1, 0)
    assert any("not a real project" in p for p in rg.check_labels())


def test_a_synthetic_project_cannot_name_a_firm_or_skip_the_prefix(golden):
    make(golden, "syn-a", agreement=False, synthetic=True, firm="Someone")
    make(golden, "oops", agreement=False, synthetic=True)
    problems = rg.check_labels()
    assert any("pick one" in p for p in problems) and any("must be named syn-" in p for p in problems)


def test_missing_or_malformed_meta_is_a_problem(golden):
    d = golden / "syn-x"
    d.mkdir()
    (d / "project.yaml").write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rg.counts()
    (d / "meta.yaml").write_text("synthetic: maybe\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rg.counts()


# ---- claims ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "The pilot is production-ready", "Now released to customers", "This build is engineer-verified", "Release candidate 1",
    "Production-ready. No known bugs.", "Release candidate: nothing blocks the pilot", "Production ready, an example of quality",
    "Version 1.0 released today", "has been released", "We have released v1", "Ready to ship", "Generally-available now",
    "prod-ready", "ready for general use", "Engine is verified by engineers", "RC1 is out: RC1", "production‑ready",
    "production ready", "The engine is fully compliant", "now in production", "PRODUCTION-READY",
])
def test_release_claims_are_detected_even_with_incidental_negations_and_odd_spacing(text):
    assert rg.claims_in(text), text


@pytest.mark.parametrize("text", [
    "Not production-ready: no real golden projects", "never pilot-ready until engineers sign",
    "This is not yet released", "Add golden evals", "release gate: tighten regex", "Released the lock on review_db",
    "the gate blocks `release candidate` wording", 'quoted "production-ready" example', "v2 schema for reachability",
    "No release is allowed before real projects exist",
])
def test_negated_or_quoted_or_unrelated_wording_is_not_a_claim(text):
    assert rg.claims_in(text) == [], text


def test_scan_covers_the_whole_repo_not_just_docs(golden, tmp_path, monkeypatch):
    make(golden, "syn-a", agreement=False, synthetic=True)
    (tmp_path / "apps" / "web").mkdir(parents=True)
    (tmp_path / "apps" / "web" / "README.md").write_text("The app is production-ready.\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("Release candidate 1\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ok.md").write_text("Status: building.\n", encoding="utf-8")
    monkeypatch.setattr(rg, "ROOT", tmp_path)
    hits = rg.scan()
    assert any("apps/web/README.md" in h for h in hits) and any("notes.txt" in h for h in hits)
    assert not any("docs/ok.md" in h for h in hits)


def test_a_version_bump_is_a_release_claim(golden, tmp_path, monkeypatch):
    make(golden, "syn-a", agreement=False, synthetic=True)
    (tmp_path / "package.json").write_text('{"name": "x", "version": "1.0.0"}', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.0.0"\n', encoding="utf-8")
    monkeypatch.setattr(rg, "ROOT", tmp_path)
    assert any("package.json: version 1.0.0" in h for h in rg.scan())
    (tmp_path / "package.json").write_text('{"version": "0.0.0"}', encoding="utf-8")
    assert not rg.version_claims()


def test_scan_is_silent_once_a_real_project_exists(golden, tmp_path, monkeypatch):
    make(golden, "pilot-1", **REAL)
    (tmp_path / "notes.md").write_text("production-ready\n", encoding="utf-8")
    monkeypatch.setattr(rg, "ROOT", tmp_path)
    assert rg.scan() == []


def test_the_commit_message_hook_blocks_claims_but_not_ordinary_wording(golden):
    make(golden, "syn-a", agreement=False, synthetic=True)
    assert rg.main(["--message", "Release candidate for the pilot"]) == 1
    assert rg.main(["--message", "release gate: tighten regex"]) == 0
    assert rg.main(["--message", "v2 schema for reachability"]) == 0
    make(golden, "pilot-1", **REAL)
    assert rg.main(["--message", "Release candidate for the pilot"]) == 0


@pytest.mark.parametrize("tag", ["v1.0.0", "1.0", "rc1", "ga", "stable", "prod", "Release-1", "release", "sprint-x", "sprint-"])
def test_only_sprint_tags_may_be_pushed_while_zero_real(golden, tag):
    make(golden, "syn-a", agreement=False, synthetic=True)
    assert rg.main(["--tag", tag]) == 1


def test_sprint_tags_are_allowed_and_everything_is_allowed_once_real_exists(golden):
    make(golden, "syn-a", agreement=False, synthetic=True)
    assert rg.main(["--tag", "sprint-2"]) == 0
    make(golden, "pilot-1", **REAL)
    assert rg.main(["--tag", "v1.0.0"]) == 0


def test_the_repo_makes_no_release_claims_today():
    assert rg.scan() == []


def test_the_git_hooks_and_ci_run_the_gate():
    for name, needle in (("pre-push", "--tag"), ("commit-msg", "--message")):
        assert needle in (ROOT / ".githooks" / name).read_text(encoding="utf-8")
    assert "check_signoff_changes" in (ROOT / ".githooks" / "pre-commit").read_text(encoding="utf-8")
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "release_gate.py --check" in ci and "release_gate.py --scan" in ci and "release:" in ci
    assert "release_gate.py --scan" in (ROOT / "scripts/ci.sh").read_text(encoding="utf-8")


def test_an_upper_case_syn_prefix_cannot_make_a_project_real(golden):
    make(golden, "SYN-fake", **REAL)
    assert rg.counts() == (1, 0)
