"""Which NCC edition may run for a state and approval date, from rules/adoption.yaml only.

This gate decides the EDITION and nothing else. Whether that edition's rule pack applies to a building class is
the separate applicability check (applicability.py, rules/applicability.yaml).

Nothing here knows a date or a state: it reads the adoption data. An edition that is not confirmed
for the project's state and approval date (unverified state, not adopted, not yet in force, past its
transition) is refused. A refusal can be overridden only by an approver, with a reason, and the
override is written to the ledger before any rule runs.
"""
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from mep.engine.adoption import VERIFIED_STATUSES, load_adoption

EDITION_KEYS = {"NCC2025": "ncc2025", "NCC2022": "ncc2022_commercial_energy"}
MIN_REASON_CHARS = 10


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reasons: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Override:
    user_id: str
    role: str
    reason: str


class OverrideRejected(Exception):
    """The override was not accepted (wrong role, no reason, or the ledger could not record it)."""


class LedgerWriter(Protocol):
    def write(self, kind: str, payload: dict[str, Any], *, firm_id: str, revision_id: str) -> None: ...


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _add_months(start: date, months: int) -> date:
    index = start.year * 12 + (start.month - 1) + months
    year, month = divmod(index, 12)
    month += 1
    for day in range(start.day, 0, -1):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 1)


def _transition_end(start: date, text: Any) -> date:
    """First day the previous edition is no longer allowed. No transition: the start itself."""
    t = str(text or "none").strip().lower()
    if m := re.fullmatch(r"(\d+)\s*months?", t):
        return _add_months(start, int(m[1]))
    if m := re.search(r"(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})", t):
        end = _date(m[2])
        if end is not None:
            return end
    return start


def decide(
    state: str,
    edition: str,
    on: date | None,
    adoption: dict[str, Any] | None = None,
) -> Decision:
    data = adoption if adoption is not None else load_adoption()
    code = (state or "").strip().upper()
    entry = data.get("jurisdictions", {}).get(code)
    if entry is None:
        return Decision(False, (f"{state}: state not in adoption data; the NCC edition is unknown",))
    if edition not in EDITION_KEYS:
        return Decision(False, (f"{code}: unknown NCC edition {edition!r}",))
    if not isinstance(on, date):
        return Decision(False, (f"{code}: no approval date; cannot confirm {edition} is in force",))
    own = entry.get(EDITION_KEYS[edition], {}) or {}
    if own.get("status") == "not adopted":
        return Decision(False, (f"{code}: {edition} is not adopted in {code}" + (f" ({own['note']})" if own.get("note") else ""),))
    if own.get("status") == "replaced":
        replaced_by = own.get("replaced_by", "a different code")
        return Decision(False, (
            f"{code}: {code} applies {replaced_by} instead of {edition} here; no rule pack covers it",))
    own_status = own.get("status") or entry.get("status")
    if own_status not in VERIFIED_STATUSES:
        note = own.get("note") or entry.get("note", "no official source")
        return Decision(False, (
            f"{code}: {edition} adoption is unverified ({note}); confirm the edition before running",))
    start = _date(own.get("from"))
    if start is None:
        return Decision(False, (f"{code}: no verified start date for {edition}",))
    if on < start:
        return Decision(False, (f"{code}: {edition} applies from {start.isoformat()}; approval date {on.isoformat()} is earlier",))

    notes: list[str] = []
    if edition == "NCC2022":
        newer = entry.get("ncc2025", {}) or {}
        new_start = _date(newer.get("from"))
        if new_start is not None and on >= new_start:
            end = _transition_end(new_start, newer.get("transition"))
            if on >= end:
                why = "no transition period" if end == new_start else f"its transition ended {end.isoformat()}"
                msg = (f"{code}: NCC2022 is superseded by NCC2025 from {new_start.isoformat()} ({why}); "
                       f"approval date {on.isoformat()}")
                return Decision(False, (msg,))
            notes.append(f"{code}: NCC2022 allowed during the NCC2025 transition until {end.isoformat()}")
    return Decision(True, (), tuple(notes))


def clean_reason(text: Any) -> str:
    """Strip control and invisible format characters (zero-width etc.) and surrounding space."""
    if not isinstance(text, str):
        return ""
    return "".join(c for c in text if unicodedata.category(c) not in ("Cc", "Cf")).strip()


def check_override(
    override: Override,
    decision: Decision,
    ledger: LedgerWriter | None,
    *,
    state: str,
    edition: str,
    approval_date: date,
    firm_id: str,
    revision_id: str,
    refused_by: tuple[str, ...] = ("jurisdiction",),
) -> None:
    """Validate an override and record it. Raises OverrideRejected; nothing is written unless valid."""
    if override.role != "approver":
        raise OverrideRejected("only an approver may override a refusal")
    if not isinstance(override.user_id, str) or not override.user_id.strip():
        raise OverrideRejected("an override needs the approver's user id")
    reason = clean_reason(override.reason)
    if len([c for c in reason if c.isalnum()]) < MIN_REASON_CHARS:
        raise OverrideRejected(f"an override needs a reason of at least {MIN_REASON_CHARS} letters or digits")
    if ledger is None:
        raise OverrideRejected("an override must be recorded in the ledger and none is available")
    payload = {
        "user_id": override.user_id, "role": override.role, "reason": reason, "state": state.upper(),
        "edition": edition, "approval_date": approval_date.isoformat(), "reasons": list(decision.reasons),
        "refused_by": list(refused_by),
    }
    try:
        kind = "jurisdiction_override" if "jurisdiction" in refused_by else "applicability_override"
        ledger.write(kind, payload, firm_id=firm_id, revision_id=revision_id)
    except Exception as exc:
        raise OverrideRejected("the override could not be recorded in the ledger") from exc
