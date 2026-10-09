"""Regenerate docs/engineer-review/disputed.md from the database (`rule_dispute`), the durable record.

The API appends to the file as disputes happen when it runs next to a checkout (development, CI); a deployed API has no checkout, so run
this script from a checkout with the service connection and commit the result for the engineers who review the rules.

    MEP_DB_URL=... uv run python scripts/export_disputed.py [--out docs/engineer-review/disputed.md]
"""
import argparse
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
HEADER = ("# Disputed rules\n\nRules a checker accepted a FAIL against with the reason category `rule_disputed`. "
          "An engineer reviews each one; nothing here changes a rule.\n\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "engineer-review" / "disputed.md")
    out = ap.parse_args().out
    dsn = os.environ.get("MEP_DB_URL", "")
    if not dsn:
        sys.exit("MEP_DB_URL is not set")
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("select d.rule_id, d.revision_id, d.reason, d.flagged_at, d.rule_result_id from rule_dispute d"
                            " order by d.flagged_at, d.id").fetchall()
    lines = [f"- `{r[0]}` disputed {r[3]:%Y-%m-%d} (revision {r[1]}): {' '.join(str(r[2]).split())[:400]} <!-- {r[4]} -->\n" for r in rows]
    out.write_text(HEADER + "".join(lines), encoding="utf-8")
    print(f"{len(rows)} dispute(s) written to {out}")


if __name__ == "__main__":
    main()
