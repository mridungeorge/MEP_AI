#!/usr/bin/env python3
"""PreToolUse (Bash): keep agents off the shared local Supabase database.

Reviewers use `python scripts/review_db.py` (a disposable database with a read-only role).
This hook blocks shell commands that name the shared DB's port or container. It cannot see
inside scripts a command runs, so the agent definition and scripts/ci.sh also enforce the rule.
"""
import json
import re
import sys

PATTERN = re.compile(r"54322|supabase_db_mep|127\.0\.0\.1:5432\b|localhost:5432\b")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command", "")
    if PATTERN.search(command):
        print(
            "Blocked by project guardrail: do not connect to the shared local database. "
            "Use `python scripts/review_db.py run -- <cmd>` (disposable DB, MEP_REVIEW_RO_URL / "
            "MEP_REVIEW_ATTACK_URL).",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
