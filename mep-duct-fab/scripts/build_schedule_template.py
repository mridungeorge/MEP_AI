"""Publish the Excel system-schedule templates to docs/templates/ (one per NCC edition).

Run after any change to rule inputs: python scripts/build_schedule_template.py
A test fails when the committed files differ from what the current rules produce.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "api")]

from mep.engine.loader import load_pack
from mep.ingest.schedule import build_template

EDITIONS = ("NCC2022", "NCC2025")


def main() -> int:
    pack = load_pack(ROOT / "rules")
    out = ROOT / "docs" / "templates"
    out.mkdir(parents=True, exist_ok=True)
    for edition in EDITIONS:
        path = out / f"mep-system-schedule-{edition}.xlsx"
        path.write_bytes(build_template(pack, edition))
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
