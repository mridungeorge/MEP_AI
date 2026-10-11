"""Load rule YAML into Rule objects and validate it against the rule schema."""
import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

NON_RULE_FILES = ("adoption.yaml", "applicability.yaml")  # data files that live beside the rules


class RuleLoadError(ValueError):
    """A rule file that is not valid YAML or does not satisfy the rule schema."""


@dataclass(frozen=True)
class InputSpec:
    name: str
    type: str
    unit: str | None
    provenance: str
    values: tuple[str, ...] | None


@dataclass
class Rule:
    raw: dict[str, Any]
    path: Path
    sha256: str

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def edition(self) -> str:
        return str(self.raw["applies_when"]["edition"])

    @property
    def states(self) -> list[str]:
        return [str(s) for s in self.raw["applies_when"]["state"]]

    @property
    def status(self) -> str:
        return str(self.raw["status"])

    @property
    def inputs(self) -> dict[str, InputSpec]:
        out: dict[str, InputSpec] = {}
        for i in self.raw["inputs"]:
            values = tuple(i["values"]) if i.get("values") else None
            out[i["name"]] = InputSpec(i["name"], i.get("type", "number"), i.get("unit"), i["provenance"], values)
        return out

    @property
    def applies_when(self) -> dict[str, Any]:
        return dict(self.raw["applies_when"])

    @property
    def thresholds(self) -> dict[str, Any]:
        return dict((self.raw.get("threshold") or {}).get("values") or {})

    @property
    def tests(self) -> list[dict[str, Any]]:
        return list(self.raw["tests"])

    def selected_for(self, edition: str, state: str) -> bool:
        """Edition must match exactly (one NCC edition per project); state must be listed or ALL;
        retired rules are never selected."""
        states = self.states
        return (self.status in ("draft", "approved") and self.edition == edition
                and ("ALL" in states or state.strip().upper() in states))


@dataclass
class RulePack:
    rules: dict[str, Rule] = field(default_factory=dict)


def _schema(rules_dir: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads((rules_dir / "schema" / "rule.schema.json").read_text(encoding="utf-8")))


def _visible(value: Any) -> str:
    """Text with control and zero-width characters removed (a blank-looking reviewer is no reviewer)."""
    return "".join(c for c in str(value or "") if unicodedata.category(c) not in ("Cc", "Cf")).strip()


def load_rule(path: Path, validator: Draft202012Validator) -> Rule:
    data = path.read_bytes()
    try:
        raw = yaml.safe_load(data)
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"{path}: invalid YAML: {exc}") from exc
    if isinstance(raw, dict):  # an unquoted YAML date is a date object; the schema wants ISO text
        for field in ("reviewed_on", "checked_on"):
            if isinstance(raw.get(field), date):
                raw[field] = raw[field].isoformat()
    errors = [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in validator.iter_errors(raw)]
    if errors:
        raise RuleLoadError(f"{path}: " + "; ".join(errors[:5]))
    from mep.engine.units import valid_unit_text  # local import: units pulls in pint

    def unit_strings(node: Any) -> list[Any]:
        found: list[Any] = []
        if isinstance(node, dict):
            if "unit" in node:
                found.append(node["unit"])
            for child in node.values():
                found += unit_strings(child)
        elif isinstance(node, list):
            for child in node:
                found += unit_strings(child)
        return found

    bad_units = [u for u in unit_strings({"i": raw.get("inputs"), "t": raw.get("threshold")}) if not valid_unit_text(u)]
    if bad_units:
        raise RuleLoadError(f"{path}: unit text outside the plain unit grammar: {bad_units[:3]}")
    signed = [f for f in ("reviewed_by", "reviewed_on", "reviewer_registration_no") if _visible(raw.get(f))]
    if raw.get("status") != "approved" and signed:
        raise RuleLoadError(f"{path}: sign-off fields {signed} on a rule that is not approved")
    if bool(_visible(raw.get("checked_by"))) != bool(_visible(raw.get("checked_on"))):
        raise RuleLoadError(f"{path}: checked_by and checked_on go together")
    if raw.get("status") == "approved" and not all(
            _visible(raw.get(f)) for f in ("reviewed_by", "reviewed_on", "reviewer_registration_no")):
        raise RuleLoadError(f"{path}: an approved rule needs reviewed_by, reviewed_on and reviewer_registration_no")
    return Rule(raw, path, hashlib.sha256(data).hexdigest())


def _guard_licensed(path: Path, rules_dir: Path) -> None:
    """A rule file in rules/licensed/<slot>/ is loaded only when a person has recorded the licence for that slot (apps/api/mep/standards_slots.yaml)."""
    parts = path.relative_to(rules_dir).parts
    if parts[0] != "licensed":
        return
    from mep.standards import load_slots

    slot = next((s for s in load_slots() if s["pack_dir"] == "/".join(parts[:2])), None)
    if slot is None or not slot["licence_held"]:
        raise RuleLoadError(f"{path}: this licensed-standard slot has no recorded licence, so no rule may be loaded from it")


def _guard_source(rule: Rule, path: Path, rules_dir: Path) -> None:
    """A rule whose `source.document` names a licensed standard belongs in that standard's licensed slot, never beside the NCC rules."""
    import re

    from mep.standards import load_slots

    if path.relative_to(rules_dir).parts[0] == "licensed":
        return
    import unicodedata

    src = rule.raw.get("source") or {}
    lookalikes = str.maketrans("АВЕКМНОРСТХаеорсх", "ABEKMHOPCTXaeopcx")
    text = unicodedata.normalize("NFKC", f"{src.get('document', '')} {src.get('clause', '')}").translate(lookalikes)
    if re.search(r"(?<![A-Za-z])AS\s*(/\s*NZS\s*)?[0-9]", text, re.IGNORECASE):
        raise RuleLoadError(f"{path}: the source cites an Australian Standard; such rules are not encoded here without a licence and belong in a licensed slot")
    document = re.sub(r"[^A-Z0-9]", "", text.upper())
    for slot in load_slots():
        if re.sub(r"[^A-Z0-9]", "", slot["standard"].upper()) in document:
            raise RuleLoadError(f"{path}: the source names {slot['standard']}, a licensed standard: its rules belong in rules/{slot['pack_dir']}/ once the licence is recorded")


def load_pack(rules_dir: Path) -> RulePack:
    validator = _schema(rules_dir)
    pack = RulePack()
    for path in sorted(rules_dir.rglob("*.yaml")):
        if any(p.is_symlink() for p in (path, *path.parents) if p == rules_dir or rules_dir in p.parents):
            raise RuleLoadError(f"{path}: rule files and folders must not be symbolic links")
        if "schema" in path.parts or path.name in NON_RULE_FILES:
            continue
        _guard_licensed(path, rules_dir)
        rule = load_rule(path, validator)
        if rule.id.startswith("AS") and "licensed" not in path.relative_to(rules_dir).parts[:1]:
            raise RuleLoadError(f"{path}: a rule of an Australian Standard belongs in its licensed slot (rules/licensed/<slot>/), never beside the NCC rules")
        _guard_source(rule, path, rules_dir)
        if rule.id in pack.rules:
            raise RuleLoadError(f"duplicate rule id {rule.id}")
        pack.rules[rule.id] = rule
    return pack
