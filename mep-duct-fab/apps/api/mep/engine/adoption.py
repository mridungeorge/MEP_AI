"""Read rules/adoption.yaml and warn when a project's state/edition is not officially confirmed."""
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ADOPTION_FILE = Path(__file__).resolve().parents[4] / "rules" / "adoption.yaml"
VERIFIED_STATUSES = {"abcb_listed", "regulator_verified"}
EDITION_KEYS = {"NCC2025": "ncc2025", "NCC2022": "ncc2022_commercial_energy"}


def load_adoption(path: Path = ADOPTION_FILE) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise TypeError("adoption.yaml must be a mapping")
    return data


def _from_date(entry: dict[str, Any]) -> date | None:
    raw = entry.get("from")
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def adoption_warnings(
    state: str,
    edition: str | None = None,
    on: date | None = None,
    data: dict[str, Any] | None = None,
) -> list[str]:
    """Warnings project setup must show. Empty list = nothing to warn about.

    With `edition` (and optionally the project date `on`) also warns when that edition is not in
    force, or not adopted, for the state. Dates come from adoption.yaml only.
    """
    adoption = data if data is not None else load_adoption()
    code = state.upper()
    entry = adoption.get("jurisdictions", {}).get(code)
    if entry is None:
        return [f"{state}: not in adoption.yaml; the NCC edition for this state is unknown"]
    warnings: list[str] = []
    if entry.get("status") not in VERIFIED_STATUSES:
        note = entry.get("note", "no official source")
        warnings.append(f"{code}: NCC adoption is unverified ({note}); confirm the edition")
    for name, ekey in EDITION_KEYS.items():  # editions that cannot be used here at all
        status = (entry.get(ekey, {}) or {}).get("status")
        if status in ("not adopted", "replaced"):
            warnings.append(f"{code}: {name} cannot be used in {code} ({status})")
    if edition is not None:
        key = EDITION_KEYS.get(edition)
        if key is None:
            warnings.append(f"{code}: unknown edition {edition}")
            return warnings
        ed = entry.get(key, {})
        start = _from_date(ed)
        if ed.get("status") == "not adopted":
            warnings.append(f"{code}: {edition} is not adopted here; do not evaluate this project against it")
        elif start is None:
            warnings.append(f"{code}: no verified start date for {edition}; confirm it is in force")
        elif on is not None and on < start:
            warnings.append(f"{code}: {edition} applies from {start.isoformat()}; the project date is earlier")
        if on is None:
            warnings.append(f"{code}: no project date given; cannot confirm {edition} is the edition in force")
        elif edition == "NCC2022":
            newer = entry.get("ncc2025", {})
            new_start = _from_date(newer)
            if new_start is not None and on >= new_start:
                transition = newer.get("transition", "none")
                warnings.append(
                    f"{code}: NCC2025 applies from {new_start.isoformat()} (transition: {transition}); "
                    "confirm NCC2022 is still allowed for this project"
                )
    return warnings
