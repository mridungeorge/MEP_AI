"""The post-filter for what an agent says about a result, and the guards on free text an agent writes.

`filter_explanation` keeps an explanation inside the facts of ONE stored result: it may cite only that result's rule id, and may name only that
result's outcome. Anything else is removed (and reported) before the text is stored or shown. This does not make the text TRUE; it makes it
impossible for an explanation to cite a rule the result does not involve or to contradict the result's outcome. The authoritative facts are
always attached beside the text by the tool layer, never produced by the model.
"""
import re
from dataclasses import dataclass, field

RULE_ID = re.compile(r"\bNCC\s?20\d\d(?:-[A-Za-z0-9]+)+\b", re.IGNORECASE)
OUTCOMES = ("PASS", "FAIL", "NEEDS_JUDGEMENT", "NOT_APPLICABLE")
OUTCOME_WORDS = re.compile(r"\b(PASS(?:ES|ED)?|FAIL(?:S|ED|URE)?|NEEDS[_ ]JUDGEMENT|NOT[_ ]APPLICABLE|COMPLIES|COMPLIANT|NON[- ]?COMPLIANT)\b", re.IGNORECASE)
REMOVED = "[removed]"
# a fix hypothesis is a suggestion to be verified: it may not announce a compliance result
COMPLIANCE_CLAIM = re.compile(r"\b(complies|compliant|non[- ]?compliant|will pass|would pass|now passes|passes|meets (?:the )?(?:requirement|ncc|code|clause)|satisfies)\b",
                              re.IGNORECASE)
_CONTROL = re.compile("[" + "".join(chr(c) for c in (*range(9), 11, 12, *range(14, 32), 127, *range(0x200B, 0x2010), *range(0x2028, 0x202F), *range(0x2060, 0x206A), 0xFEFF)) + "]")


@dataclass
class Filtered:
    text: str
    redactions: list[str] = field(default_factory=list)


def clean_text(text: str, limit: int) -> str:
    """Control and direction-override characters removed, whitespace normalised, length bounded."""
    return " ".join(_CONTROL.sub("", text).split())[:limit]


def _normal(rule_id: str) -> str:
    return re.sub(r"[\s_]+", "-", rule_id.strip()).upper()


def filter_explanation(text: str, rule_id: str, outcome: str) -> Filtered:
    """Remove rule ids other than `rule_id`, and outcome words other than `outcome`."""
    text = clean_text(text, 4000)
    red: list[str] = []
    allowed = _normal(rule_id)

    def rule(m: re.Match[str]) -> str:
        if _normal(m.group(0)) == allowed:
            return rule_id
        red.append(f"rule id {m.group(0)!r}")
        return REMOVED

    text = RULE_ID.sub(rule, text)
    want = outcome.upper().replace("_", " ")

    def word(m: re.Match[str]) -> str:
        w = m.group(0).upper().replace("_", " ")
        base = {"PASSES": "PASS", "PASSED": "PASS", "FAILS": "FAIL", "FAILED": "FAIL", "FAILURE": "FAIL"}.get(w, w)
        if base == want:
            return m.group(0)
        red.append(f"outcome word {m.group(0)!r}")
        return REMOVED

    text = OUTCOME_WORDS.sub(word, text)
    return Filtered(text.strip(), red)


def check_hypothesis(text: str, rule_id: str) -> tuple[str, str | None]:
    """(cleaned text, refusal reason or None) for a fix hypothesis about the result of `rule_id`."""
    text = clean_text(text, 1000)
    if len(text) < 10:
        return text, "a fix hypothesis needs at least a sentence"
    if COMPLIANCE_CLAIM.search(text):
        return text, "a hypothesis may not claim compliance or a passing result: it is something to verify"
    other = [m.group(0) for m in RULE_ID.finditer(text) if _normal(m.group(0)) != _normal(rule_id)]
    if other:
        return text, f"a hypothesis about this result may not cite another rule ({other[0]})"
    return text, None
