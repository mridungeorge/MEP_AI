"""CI check: the canonical building-class list covers every rule's declared classes and every refusal."""
from pathlib import Path

import yaml
from mep.engine.applicability import load_applicability

ROOT = Path(__file__).resolve().parents[2]
DATA = load_applicability()


def test_the_canonical_building_class_list_covers_every_rule_and_refusal():
    classes = {str(c) for c in DATA["building_classes"]}
    assert classes
    for carve in DATA["carve_outs"]:
        assert set(carve["classes"]) <= classes, carve["id"]
    for f in (ROOT / "rules").rglob("NCC20*.yaml"):
        rule = yaml.safe_load(f.read_text(encoding="utf-8"))
        for i in rule["inputs"]:
            if i["name"] == "building_class":
                assert set(i.get("values", [])) <= classes, f.name
                for key, spec in rule["applies_when"].items():
                    if key == "building_class":
                        assert set(spec if isinstance(spec, list) else spec.get("in", [])) <= classes, f.name
