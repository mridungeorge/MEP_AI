"""Licensed-standard slots stay empty until a person records a licence: no rule file may sit in a slot whose licence_held is false, and no licensed-standard rule
may hide in the NCC folders."""
from pathlib import Path

import pytest
from mep import standards

ROOT = Path(__file__).resolve().parents[2]
RULES = ROOT / "rules"


def test_the_four_slots_exist_are_unlicensed_and_empty():
    slots = standards.load_slots()
    assert {s["id"] for s in slots} == {"as1668-2", "as-nzs-3000", "as-nzs-3008", "as4254"}
    for s in slots:
        assert (RULES / s["pack_dir"]).is_dir(), s["id"]
    states = {x["id"]: x for x in standards.all_states(RULES)}
    assert all(x["state"] == "LICENCE_REQUIRED" and x["rules_loaded"] == 0 and "Licence required" in x["message"] for x in states.values())


def test_no_rule_file_sits_in_an_unlicensed_slot():
    for s in standards.load_slots():
        if not s["licence_held"]:
            assert standards.rule_files(s, RULES) == [], f"{s['id']} has rule files but licence_held is false"


@pytest.mark.parametrize("needle", ["AS1668", "AS 1668", "AS3000", "AS/NZS 3000", "AS/NZS 3008", "AS3008", "AS 4254", "AS4254"])
def test_no_ncc_rule_file_claims_a_licensed_standard_as_its_source(needle):
    for path in (RULES / "ncc2022", RULES / "ncc2025"):
        for f in path.rglob("*.yaml"):
            text = f.read_text(encoding="utf-8")
            source_lines = [ln for ln in text.splitlines() if ln.strip().startswith(("document:", "source:"))]
            assert not any(needle in ln for ln in source_lines), f"{f} cites {needle} as its source"


def test_the_slot_states_follow_the_flags(tmp_path):
    folder = tmp_path / "licensed" / "x"
    folder.mkdir(parents=True)
    slot = {"id": "x", "standard": "AS X", "title": "t", "discipline": "mechanical", "pack_dir": "licensed/x", "licence_held": False}
    assert standards.slot_state(slot, tmp_path)["state"] == "LICENCE_REQUIRED"
    assert standards.slot_state({**slot, "licence_held": True}, tmp_path)["state"] == "NO_RULES"
    (folder / "r.yaml").write_text("id: x\n", encoding="utf-8")
    assert standards.slot_state({**slot, "licence_held": True}, tmp_path)["state"] == "READY"
    assert standards.slot_state(slot, tmp_path)["state"] == "LICENCE_REQUIRED"          # a file does not switch a licence on


def test_the_engine_refuses_a_rule_dropped_into_an_unlicensed_slot_or_beside_the_ncc_rules(tmp_path):
    import shutil

    from mep.engine.loader import RuleLoadError, load_pack
    one = next((RULES / "ncc2025").rglob("*.yaml"))
    for where, needle in (("licensed/as1668-2", "no recorded licence"), ("as1668", "belongs in its licensed slot")):
        rules = tmp_path / where.replace("/", "_")
        shutil.copytree(RULES, rules)
        target = rules / where
        target.mkdir(parents=True, exist_ok=True)
        text = one.read_text(encoding="utf-8")
        if where == "as1668":
            text = text.replace(next(ln for ln in text.splitlines() if ln.startswith("id:")), "id: AS1668.2-5.1-made-up")
        (target / "x.yaml").write_text(text, encoding="utf-8")
        with pytest.raises(RuleLoadError, match=needle):
            load_pack(rules)


@pytest.mark.parametrize("document", ["AS 3000:2018", "AS/NZS 1668.2", "AS 1668 Part 2", "ＡＳ 1668.2", "АS 1668.2", "AS 1851-2012"])
def test_spellings_of_australian_standards_are_refused_outside_a_licensed_slot(tmp_path, document):
    import shutil

    from mep.engine.loader import RuleLoadError, load_pack
    rules = tmp_path / "rules"
    shutil.copytree(RULES, rules)
    one = next((rules / "ncc2025").rglob("*.yaml"))
    text = one.read_text(encoding="utf-8")
    import re
    one.write_text(re.sub(r"(?m)^(\s*document:).*$", lambda m: f'{m.group(1)} "{document}"', text, count=1), encoding="utf-8")
    with pytest.raises(RuleLoadError):
        load_pack(rules)
