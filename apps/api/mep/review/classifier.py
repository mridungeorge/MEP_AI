"""Exception classifier: how much human attention each stored result needs at Gate 2.

Pure and deterministic. It decides nothing about compliance; it only sorts results into

* `clean_pass`: the only class a checker may approve in bulk (after a random spot-check), and
* exceptions, which are reviewed one by one with a reason. Their class is the MOST SERIOUS reason that applies; every reason is listed.

Order, most serious first: fail, needs_judgement, stale, flipped, near_miss, not_applicable, lookup_input, clean_pass.

A result is a clean pass only when ALL hold: the outcome is PASS; it is not a near miss (the rule's own near-miss test, which must
have varied every input it could: a rule that declares no near-miss fraction has nothing to test and is judged on the rest); no cause is
recorded; it is not stale; the same result in the parent revision (if there is one) was also a PASS; and every input it used was
confirmed by the engineer, not read from an address lookup. Anything the classifier cannot read is NOT a clean pass.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

CLASSES = ("fail", "needs_judgement", "stale", "flipped", "near_miss", "not_applicable", "lookup_input", "clean_pass")
EXCEPTIONS = CLASSES[:-1]
CLEAN = "clean_pass"
_LOOKUP = "address_lookup_confirmed"


@dataclass(frozen=True)
class Classification:
    klass: str
    reasons: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return self.klass == CLEAN


def _outcome(r: Mapping[str, Any] | None) -> str | None:
    if r is None:
        return None
    value = r.get("outcome", r.get("result"))
    return None if value is None else str(value)


def _lookup_inputs(r: Mapping[str, Any]) -> list[str]:
    used = r.get("inputs_used", r.get("inputs"))
    if not isinstance(used, Mapping):
        return []
    return sorted(str(name) for name, v in used.items() if isinstance(v, Mapping) and v.get("provenance") == _LOOKUP)


def classify(result: Mapping[str, Any], parent: Mapping[str, Any] | None = None) -> Classification:
    """The class of one stored result; `parent` is the same subject/rule's result in the parent revision, if any."""
    outcome = _outcome(result)
    reasons: dict[str, str] = {}
    if outcome == "FAIL":
        reasons["fail"] = "the rule failed"
    elif outcome == "NEEDS_JUDGEMENT":
        reasons["needs_judgement"] = "the rule needs an engineering judgement"
    elif outcome == "NOT_APPLICABLE":
        reasons["not_applicable"] = "the rule was found not to apply: confirm that is right"
    elif outcome != "PASS":
        reasons["needs_judgement"] = f"unreadable outcome {outcome!r}"
    if result.get("stale"):
        reasons["stale"] = "the revision changed something this result depends on; it has not been re-run"
    before = _outcome(parent)
    if before is not None and outcome is not None and before != outcome:
        reasons["flipped"] = f"the outcome changed from {before} in the parent revision"
    near = result.get("near_miss")
    if isinstance(near, Mapping) and near.get("is_near_miss"):
        reasons["near_miss"] = "a small change to an input would flip the outcome"
    elif isinstance(near, Mapping) and near.get("not_evaluated"):
        reasons["near_miss"] = "the near-miss test could not vary every input (" + ", ".join(
            sorted(str(x) for x in near["not_evaluated"])) + ")"
    elif outcome == "PASS" and result.get("near_miss") is not None and not isinstance(near, Mapping):
        reasons["near_miss"] = "the near-miss record is unreadable"
    causes = result.get("causes")
    if causes and outcome == "PASS":
        reasons["needs_judgement"] = "a pass that recorded causes is not clean"
    lookups = _lookup_inputs(result)
    if lookups:
        reasons["lookup_input"] = "relies on an input taken from an address lookup: " + ", ".join(lookups)
    for klass in EXCEPTIONS:
        if klass in reasons:
            return Classification(klass, tuple(reasons[k] for k in EXCEPTIONS if k in reasons))
    return Classification(CLEAN, ())


def classify_all(results: list[Mapping[str, Any]], parents: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> dict[
        tuple[str, str], Classification]:
    """{(subject_id, rule_id): classification} for a revision's current results."""
    parents = parents or {}
    return {(str(r["subject_id"]), str(r["rule_id"])): classify(r, parents.get((str(r["subject_id"]), str(r["rule_id"]))))
            for r in results}
