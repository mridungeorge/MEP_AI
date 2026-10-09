from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yaml
from mep.engine.adoption import adoption_warnings, load_adoption

DATA = load_adoption()
STATES = {"NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT"}


def test_every_state_present_with_status():
    assert set(DATA["jurisdictions"]) == STATES
    for entry in DATA["jurisdictions"].values():
        assert entry["status"] in {"abcb_listed", "regulator_verified", "unverified"}


def test_sources_are_official_government_hosts():
    for url in DATA["sources"].values():
        host = urlparse(url).hostname or ""
        assert host.endswith(".gov.au"), url
    text = (Path(__file__).resolve().parents[2] / "rules/adoption.yaml").read_text()
    assert "thegoodbuilder" not in text and "climatecontrolnews" not in text


def test_unverified_inputs_are_marked_unverified():
    for state, entry in DATA["jurisdictions"].items():
        if "UNVERIFIED" in yaml.safe_dump(entry):
            assert entry["status"] == "unverified", state


def test_project_setup_warns_on_unverified_states():
    assert adoption_warnings("QLD") and adoption_warnings("NT")
    assert adoption_warnings("qld")  # case-insensitive
    assert adoption_warnings("VIC") == []
    assert adoption_warnings("XX")  # unknown state is also a warning


def test_edition_not_adopted_or_not_yet_in_force_warns():
    assert any("not adopted" in w for w in adoption_warnings("NT", "NCC2025"))
    assert any("applies from 2027-05-01" in w for w in adoption_warnings("NSW", "NCC2025", date(2026, 10, 6)))
    assert adoption_warnings("NSW", "NCC2025", date(2027, 6, 1)) == []
    assert adoption_warnings("VIC", "NCC2025", date(2026, 10, 6)) == []
    assert any("NCC2025 applies from 2026-05-01" in w for w in adoption_warnings("VIC", "NCC2022", date(2026, 10, 6)))
    assert any("no project date" in w for w in adoption_warnings("VIC", "NCC2025"))
    assert not any("NCC2025 applies" in w for w in adoption_warnings("NSW", "NCC2022", date(2026, 10, 6)))
    assert any("no verified start date" in w for w in adoption_warnings("QLD", "NCC2022"))
    assert any("unknown edition" in w for w in adoption_warnings("VIC", "NCC2030"))


def test_rule_packs_do_not_name_states_where_the_edition_is_unavailable():
    """Explicit state lists in the packs must not include NT (neither pack is confirmed there)."""
    for f in (Path(__file__).resolve().parents[2] / "rules").rglob("NCC20*.yaml"):
        states = yaml.safe_load(f.read_text(encoding="utf-8"))["applies_when"]["state"]
        assert "NT" not in states, f.name


def _urls(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("source", "also") and isinstance(v, str):
                yield v
            yield from _urls(v)


def test_every_cited_url_is_an_official_government_host():
    for url in _urls(DATA):
        host = urlparse(url).hostname or ""
        assert host.endswith(".gov.au"), url


def test_each_edition_entry_has_a_status_and_verified_ones_cite_a_source():
    for state, entry in DATA["jurisdictions"].items():
        for key in ("ncc2025", "ncc2022_commercial_energy"):
            ed = entry[key]
            assert ed["status"] in {"regulator_verified", "abcb_listed", "unverified", "not adopted", "replaced"}, (state, key)
            if ed["status"] in {"regulator_verified", "abcb_listed", "not adopted", "replaced"}:
                assert ed["source"], (state, key)
            if ed["status"] == "unverified":
                assert str(ed.get("from")) == "UNVERIFIED" or "from" not in ed, (state, key)
