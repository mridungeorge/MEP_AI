"""The post-filters on what an agent says, and the guards on free text an agent writes.

Nothing here makes an agent's words TRUE; it makes some false statements impossible to publish: an explanation of one result may cite only that
result's rule id, name only that result's outcome and make no compliance statement; a fix hypothesis may not announce a passing or compliant
result; the model's free-text reply may cite only rule ids of this revision's results and contains no outcome or compliance wording at all (outcomes
come only from the stored results, shown beside the text by the server). Text is folded first (NFKC, every dash to '-', look-alike letters to Latin,
invisible characters removed) so look-alike spellings do not get past the patterns. Numbers and thresholds in prose are NOT checked: the docs say so.
"""
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

REMOVED = "[removed]"
RULE_ID = re.compile(r"\bNCC[\s_-]?20\d\d(?:[-_][A-Za-z0-9]+)+\b", re.IGNORECASE)
CLAUSE = re.compile(r"\b[A-J]\d{1,2}[A-Z]\d{1,2}\b", re.IGNORECASE)
# any word that states or implies an outcome / compliance
CLAIM_STEMS = re.compile(r"\b(pass(?:es|ed|ing)?(?![-\w])|fail(?:s|ed|ing|ure|ures)?\b|compl(?:y|ies|ied|ying|iance|iant)\b|non[- ]?compl\w*"
                         r"|conform(?:s|ed|ing|ance|ant)?\b|satisf(?:y|ies|ied|ying|actory)\b|accept(?:s|ed|ing|able|ance)?\b|approv(?:e|es|ed|ing|al)\b"
                         r"|meets?|met|ok|okay|cumple|green|fine|within (?:the )?limits?|conformity|adher\w*|success\w*|sufficient|resolved|clears? the)\b"
                         "|[✅✔☑❌✖]", re.IGNORECASE)
NEEDS_JUDGEMENT = re.compile(r"\bneeds?[_ ]judg\w*", re.IGNORECASE)
NOT_APPLICABLE = re.compile(r"\bnot[_ ]applicable\b", re.IGNORECASE)
OWN_STEMS = {"PASS": r"pass(?:es|ed|ing)?", "FAIL": r"fail(?:s|ed|ing|ure|ures)?", "NEEDS_JUDGEMENT": r"needs?[_ ]judg\w*", "NOT_APPLICABLE": r"not[_ ]applicable"}
# a hypothesis may say a result FAILS; it may not say anything will pass / comply / meet / be accepted
HYPOTHESIS_CLAIM = re.compile(r"\b(pass(?:es|ed|ing)?(?![-\w])|compl(?:y|ies|ied|ying|iance|iant)\b|non[- ]?compl\w*|conform\w*|satisf\w*|accept\w*|approv\w*"
                              r"|meets?|ok|okay|achiev\w*|cumple|no longer fail\w*|green|fine|within (?:the )?limits?|conformity|adher\w*|success\w*|sufficient|resolved|clears? the)\b"
                              "|[✅✔☑]", re.IGNORECASE)
LOOKALIKES = str.maketrans({
    "А": "A", "В": "B", "С": "C", "Е": "E", "Н": "H", "К": "K", "М": "M", "О": "O", "Р": "P",
    "Т": "T", "Х": "X", "а": "a", "с": "c", "е": "e", "о": "o", "р": "p", "х": "x", "у": "y",
    "Α": "A", "Β": "B", "Ε": "E", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O",
    "Ρ": "P", "Τ": "T", "Χ": "X", "ο": "o", "−": "-",
})
HANGUL_FILLERS = "\u115f\u1160\u3164\uffa0"
KEEP_SYMBOLS = "\u00b0\u00b1\u00d7\u00f7\u00b2\u00b3\u00b5\u2264\u2265\u2248\u00d8\u00b7\u2013\u2014\u2022\u00a7\u00a9\u20ac\u00a3\u2192"
_HIDDEN = "".join(chr(c) for c in (*range(9), 11, 12, *range(14, 32), 127, *range(0x200B, 0x2010), *range(0x2028, 0x202F), *range(0x2060, 0x206A), 0xFEFF))
_HIDDEN_RE = re.compile("[" + re.escape(_HIDDEN) + "]")


@dataclass
class Filtered:
    text: str
    redactions: list[str] = field(default_factory=list)


def fold(text: str) -> str:
    """NFKC, every dash-like character to '-', look-alike Cyrillic/Greek letters to Latin, hidden characters removed."""
    text = _HIDDEN_RE.sub("", unicodedata.normalize("NFKC", text))
    text = re.sub(r"\b(?:[A-Za-z][.\- ]){3,}[A-Za-z]\b", lambda m: re.sub(r"[.\- ]", "", m.group(0)), text)      # P.A.S.S / p-a-s-s
    text = "".join("-" if unicodedata.category(c) == "Pd" else c for c in text).translate(LOOKALIKES)
    out = []
    for ch in unicodedata.normalize("NFKD", text):
        cat = unicodedata.category(ch)
        if cat in ("Cf", "Mn", "Me", "Co", "Cn", "Cs") or ch in HANGUL_FILLERS:
            continue                                                  # invisible or combining: gone
        if ord(ch) > 127 and (cat[0] == "L" or (cat[0] == "S" and ch not in KEEP_SYMBOLS)):
            out.append("?")                                           # a letter or symbol we cannot read as Latin must not pose as one
        else:
            out.append(ch)
    return "".join(out)


def clean_text(text: str, limit: int) -> str:
    """Folded, whitespace normalised, bounded."""
    return " ".join(fold(text).split())[:limit]


def _normal(rule_id: str) -> str:
    return re.sub(r"[\s_]+", "-", fold(rule_id).strip()).upper()


def _sub_rules(text: str, allowed: set[str], red: list[str], keep_as: dict[str, str] | None = None) -> str:
    def rule(m: re.Match[str]) -> str:
        n = _normal(m.group(0))
        if n in allowed:
            return (keep_as or {}).get(n, m.group(0))
        red.append(f"rule id {m.group(0)!r}")
        return REMOVED

    return RULE_ID.sub(rule, text)


def _outside_ids(text: str, fn: Callable[[str], str]) -> str:
    """Apply `fn` to the stretches of `text` that are not rule ids (the ids that remain were already checked and are the result's own)."""
    out, last = [], 0
    for m in RULE_ID.finditer(text):
        out.append(fn(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(fn(text[last:]))
    return "".join(out)


def _sub_clauses(text: str, own_clause: str | None, red: list[str]) -> str:
    own = (own_clause or "").upper().replace(" ", "")

    def clause(m: re.Match[str]) -> str:
        if own and own.startswith(m.group(0).upper()):
            return m.group(0)
        red.append(f"clause {m.group(0)!r}")
        return REMOVED

    return _outside_ids(text, lambda t: CLAUSE.sub(clause, t))


def filter_explanation(text: str, rule_id: str, outcome: str, clause: str | None = None, limit: int = 3900) -> Filtered:
    """Keep an explanation inside ONE stored result: its own rule id, its own outcome, no compliance statement, no other clause."""
    text = clean_text(text, 20000)
    red: list[str] = []
    text = _sub_rules(text, {_normal(rule_id)}, red, {_normal(rule_id): rule_id})
    own = re.compile(r"(?:" + OWN_STEMS.get(outcome.upper(), re.escape(outcome)) + r")", re.IGNORECASE)

    def word(m: re.Match[str]) -> str:
        if own.fullmatch(m.group(0)):
            return m.group(0)
        red.append(f"outcome or compliance word {m.group(0)!r}")
        return REMOVED

    def words(t: str) -> str:
        t = CLAIM_STEMS.sub(word, t)
        for pat in (NEEDS_JUDGEMENT, NOT_APPLICABLE):
            t = pat.sub(lambda m: m.group(0) if own.fullmatch(m.group(0)) else (red.append(f"outcome word {m.group(0)!r}") or REMOVED), t)
        return t

    text = _outside_ids(text, words)
    text = _sub_clauses(text, clause, red)
    return Filtered(text.strip()[:limit], red)


def filter_free_text(text: str, allowed_rule_ids: Iterable[str], limit: int = 3900) -> Filtered:
    """The model's own chat reply. It may mention rule ids of THIS revision's results, but no outcome or compliance wording and no clause number:
    outcomes are shown only from the stored results."""
    text = clean_text(text, 20000)
    red: list[str] = []
    text = _sub_rules(text, {_normal(r) for r in allowed_rule_ids}, red)
    def words(t: str) -> str:
        t = CLAIM_STEMS.sub(lambda m: (red.append(f"outcome or compliance word {m.group(0)!r}") or REMOVED), t)
        t = NEEDS_JUDGEMENT.sub(lambda m: (red.append(f"outcome word {m.group(0)!r}") or REMOVED), t)
        return NOT_APPLICABLE.sub(lambda m: (red.append(f"outcome word {m.group(0)!r}") or REMOVED), t)

    text = _outside_ids(text, words)
    text = _sub_clauses(text, None, red)
    return Filtered(text.strip()[:limit], red)


def check_hypothesis(text: str, rule_id: str) -> tuple[str, str | None]:
    """(cleaned text, refusal reason or None) for a fix hypothesis about the result of `rule_id`."""
    text = clean_text(text, 1000)
    if len(text) < 10:
        return text, "a fix hypothesis needs at least a sentence"
    if HYPOTHESIS_CLAIM.search(text) or NEEDS_JUDGEMENT.search(text) or re.search(r"\boutcome\b.{0,20}\b(become|change|turn)", text, re.IGNORECASE):
        return text, "a hypothesis may not claim compliance or a passing result: it is something to verify"
    other = [m.group(0) for m in RULE_ID.finditer(text) if _normal(m.group(0)) != _normal(rule_id)]
    if other:
        return text, f"a hypothesis about this result may not cite another rule ({other[0]})"
    return text, None
