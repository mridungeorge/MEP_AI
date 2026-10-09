"""The PreToolUse guards must fire for the PowerShell tool exactly as for Bash.

Two things are proved: (1) in .claude/settings.json every hook entry that matches Bash also matches PowerShell, and
(2) the sign-off guard and the shared-database guard block PowerShell-style commands (same `tool_input.command` field).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SIGNOFF = ROOT / ".claude/hooks/guard_signoff_bash.py"
REVIEW_DB = ROOT / ".claude/hooks/guard_review_db.py"


def run(hook: Path, command: str, tool: str = "PowerShell") -> int:
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(hook)], input=payload, text=True, capture_output=True, check=False).returncode


def test_every_hook_matching_bash_also_matches_powershell():
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    seen = 0
    for event, entries in settings["hooks"].items():
        for entry in entries:
            tools = set(entry.get("matcher", "").split("|"))
            if "Bash" in tools:
                seen += 1
                assert "PowerShell" in tools, f"{event}: matcher {entry['matcher']!r} covers Bash but not PowerShell"
    assert seen >= 2          # the sign-off guard and the shared-database guard


def test_the_two_guards_are_registered_for_powershell():
    settings = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    for script in ("guard_signoff_bash.py", "guard_review_db.py"):
        matchers = [e["matcher"] for e in settings["hooks"]["PreToolUse"]
                    if any(script in " ".join(h.get("args", [])) for h in e["hooks"])]
        assert matchers and all("PowerShell" in m.split("|") for m in matchers), script


SIGNOFF_BLOCKED = [
    "Set-Content rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml 'reviewed_by: Jane'",
    "(Get-Content rules/ncc2025/j6/x.yaml) -replace 'status: draft','status: approved' | Set-Content rules/ncc2025/j6/x.yaml",
    "(gc rules/ncc2022/j6/x.yaml) -replace 'checked_by: null','checked_by: Jane' | sc rules/ncc2022/j6/x.yaml",
    "Add-Content -Path rules\\ncc2022\\j6\\x.yaml -Value 'reviewed_on: 2026-01-01'",
    "'status: approved' | Out-File -Append rules/ncc2025/j6/x.yaml",
    "[IO.File]::WriteAllText('rules/ncc2025/j6/x.yaml','reviewer_registration_no: 1')",
    "Copy-Item approved.yaml rules/ncc2025/j6/x.yaml",
    "Set-Content rules/ncc2025/j6/x.yaml 'status: approved'",
    "$env:MEP_HUMAN_SIGNOFF = 'Jane Engineer RPEQ 12345'; git commit -m x",
    "git commit --no-verify -m x",
    "git commit -nm x",
    "git -c core.hooksPath=NUL commit -m x",
    "git config core.hooksPath C:\\empty",
    "Set-Content evals/golden/syn-a/meta.yaml 'data_agreement: signed'",
    "Set-Content rules/ncc2025/j6/x.yaml 'revie`wed_by: Jane'",      # a PowerShell backtick splice
]


@pytest.mark.parametrize("command", SIGNOFF_BLOCKED)
def test_signoff_guard_blocks_powershell(command):
    assert run(SIGNOFF, command) == 2, command


SIGNOFF_ALLOWED = [
    "Get-ChildItem rules -Recurse",
    "Get-Content rules/ncc2025/j6/x.yaml",
    "Set-Content notes.txt 'hello'",
    "git status --short",
    "git commit -m 'tidy'",
    "uv run pytest tests/rules -q",
]


@pytest.mark.parametrize("command", SIGNOFF_ALLOWED)
def test_signoff_guard_allows_ordinary_powershell(command):
    assert run(SIGNOFF, command) == 0, command


DB_BLOCKED = [
    "psql -h 127.0.0.1 -p 54322 -U postgres",
    "docker exec supabase_db_mep psql -U postgres",
    "Invoke-Sqlcmd -ConnectionString 'host=127.0.0.1:5432'",
    "$env:DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/postgres'; python x.py",
    "psql postgresql://postgres:postgres@127.0.0.1:54322/postgres -c 'select 1'",
]


@pytest.mark.parametrize("command", DB_BLOCKED)
def test_shared_database_guard_blocks_powershell(command):
    assert run(REVIEW_DB, command) == 2, command


@pytest.mark.parametrize("command", ["Get-ChildItem", "python scripts/review_db.py selftest", "docker ps"])
def test_shared_database_guard_allows_other_powershell(command):
    assert run(REVIEW_DB, command) == 0, command


def test_bash_behaviour_is_unchanged():
    assert run(SIGNOFF, "sed -i 's/status: draft/status: approved/' rules/ncc2022/j6/a.yaml", tool="Bash") == 2
    assert run(REVIEW_DB, "psql -p 54322", tool="Bash") == 2
    assert run(SIGNOFF, "ls rules", tool="Bash") == 0
