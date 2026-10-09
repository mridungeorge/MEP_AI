"""Detect writes of human sign-off fields in rule YAML, by comparing what a file contains before and after.

Used by the Edit/Write hook (.claude/hooks/guard_rules.py), the Bash hook and the pre-commit tripwire
(.githooks/pre-commit via scripts/check_signoff_changes.py). It parses YAML rather than matching text, so quoted keys,
flow style, explicit keys, merge keys, extra documents and tag tricks are all seen. Where YAML cannot be parsed it falls
back to a text scan that fails closed.

The fields are typed by the human engineer: `reviewed_by`, `reviewed_on`, `reviewer_registration_no` (approval) and
`checked_by`, `checked_on` (the non-approving first-pass value check), plus `status` becoming `approved`.
Stdlib only, except PyYAML when it is installed.
"""
import re
import unicodedata
from typing import Any

try:
    import yaml
except ImportError:  # the hook may run on a bare interpreter: fall back to the text scan
    yaml = None  # type: ignore[assignment]

FIELDS = ("reviewed_by", "reviewed_on", "reviewer_registration_no", "checked_by", "checked_on")
_FALLBACK = re.compile(
    r"""(?ix)
    ["']?\b(?P<key>reviewed_by|reviewed_on|reviewer_registration_no|checked_by|checked_on)\b["']?\s*:\s*
    (?!null\b|~|["']{2}\s*(?:[,}\n]|$)|\s*(?:[,}\n]|$))\S
    """
)
_FALLBACK_STATUS = re.compile(r"""(?ix)["']?\bstatus\b["']?\s*:\s*(?:!!\w+\s+|[|>][-+]?\s*)?["']?approved\b""")


def visible(value: Any) -> str:
    """Text with control and zero-width characters removed (an invisible name is no name)."""
    text = "" if value is None else str(value)
    return "".join(c for c in unicodedata.normalize("NFKC", text) if unicodedata.category(c) not in ("Cc", "Cf")).strip()


def _documents(text: str) -> list[dict[str, Any]] | None:
    if yaml is None:
        return None
    try:
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
    except yaml.YAMLError:
        return None
    return [d for d in docs if isinstance(d, dict)] if all(isinstance(d, dict) for d in docs) else None


def _state(docs: list[dict[str, Any]]) -> dict[str, str]:
    """Merge the sign-off fields of every document (later documents win only by being non-empty)."""
    out = {f: "" for f in (*FIELDS, "status")}
    for doc in docs:
        for f in out:
            v = visible(doc.get(f))
            if v:
                out[f] = v
    return out


def violations(old_text: str, new_text: str) -> list[str]:
    """Reasons the change from old_text to new_text sets a human sign-off field. Empty list = fine."""
    found: list[str] = []
    old_docs, new_docs = _documents(old_text), _documents(new_text)
    if old_docs is not None and new_docs is not None:
        old, new = _state(old_docs), _state(new_docs)
        for f in FIELDS:
            if new[f] and new[f] != old[f]:
                found.append(f"sets {f}")
        if new["status"].lower() == "approved" and old["status"].lower() != "approved":
            found.append("sets status: approved")
        return found
    # YAML unavailable or unparseable: fail closed with a text scan of the NEW text, ignoring what was already there
    old_hits = {m.group(0) for m in _FALLBACK.finditer(old_text)}
    for m in _FALLBACK.finditer(new_text):
        if m.group(0) not in old_hits:
            found.append(f"sets {m.group('key')} (text scan: the file could not be parsed as YAML)")
    if _FALLBACK_STATUS.search(new_text) and not _FALLBACK_STATUS.search(old_text):
        found.append("sets status: approved (text scan)")
    return found


class EditDoesNotMatch(ValueError):
    """An Edit's old_string is not in the file text: the change cannot be judged, so it is refused."""


def apply_edits(old_text: str, tool_input: dict[str, Any]) -> str:
    """The file text after an Edit or MultiEdit tool call (Write is handled by the caller).

    Raises EditDoesNotMatch if an old_string cannot be found (also tried with CRLF folded to LF): a change we cannot
    reconstruct must not be waved through as 'nothing changed'.
    """
    text = old_text
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        edits = [tool_input]
    for edit in edits:
        if not isinstance(edit, dict):
            raise EditDoesNotMatch("malformed edit")
        old, new = edit.get("old_string"), edit.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise EditDoesNotMatch("edit without old_string/new_string")
        if old == "":
            if text != "":
                raise EditDoesNotMatch("an Edit with an empty old_string can only create a new file")
            text = new  # creating a file: the whole new text is judged against the empty file
            continue
        folded = text.replace("\r\n", "\n")
        old_f, new_f = old.replace("\r\n", "\n"), new.replace("\r\n", "\n")
        if old in text:
            text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
        elif old_f in folded:
            text = folded.replace(old_f, new_f) if edit.get("replace_all") else folded.replace(old_f, new_f, 1)
        else:
            raise EditDoesNotMatch("old_string was not found in the file")
    return text


def _clean_component(part: str) -> str:
    """Windows ignores trailing dots and spaces and an ::$DATA stream suffix: drop them so aliases cannot hide a path."""
    part = part.split("::")[0]
    return part.rstrip(". ")


def is_rule_path(path: str) -> bool:
    """True for any file the engine would load as a rule. Consistent with the loader: 'rules' and the suffix are matched
    case-insensitively (Windows), but the exclusions (schema folder, adoption/applicability data) are exact names, so
    a file named ADOPTION.yaml or a folder named Schema is still guarded."""
    parts = [_clean_component(c) for c in path.replace("\\", "/").split("/") if c not in ("", ".")]
    if not parts:
        return False
    lowered = [c.lower() for c in parts]
    return ("rules" in lowered and lowered[-1].endswith((".yaml", ".yml")) and "schema" not in parts
            and parts[-1] not in ("adoption.yaml", "applicability.yaml"))


def real_paths(path: str) -> list[str]:
    """The path as given plus its resolved form (symlinks and junctions followed), so a link cannot hide a rule file."""
    import os

    out = [path]
    try:
        out.append(os.path.realpath(path))
    except (OSError, ValueError):
        pass
    return out


def is_golden_real_path(path: str) -> bool:
    """Files that make a golden project REAL: its meta.yaml and any data_agreement.* (a human supplies these)."""
    parts = [_clean_component(c) for c in path.replace("\\", "/").split("/") if c]
    low = [c.lower() for c in parts]
    return "golden" in low and "evals" in low and (low[-1] == "meta.yaml" or low[-1].startswith("data_agreement"))


def golden_real_change(old_text: str, new_text: str) -> bool:
    """True if a meta.yaml change sets `synthetic: false` (a project becoming REAL) or fills the real-project fields."""
    def meta(text: str) -> dict[str, Any]:
        if yaml is None:
            return {"__unparsed__": True}
        try:
            data = yaml.safe_load(text) if text.strip() else {}
        except yaml.YAMLError:
            return {"__unparsed__": True}
        return data if isinstance(data, dict) else {}

    old, new = meta(old_text), meta(new_text)
    if new.get("__unparsed__") and not old.get("__unparsed__"):
        return True  # cannot judge: refuse
    if new.get("synthetic") is False and old.get("synthetic") is not False:
        return True
    return any(visible(new.get(f)) and visible(new.get(f)) != visible(old.get(f))
               for f in ("firm", "data_agreement", "engineer_signed_off_by"))
