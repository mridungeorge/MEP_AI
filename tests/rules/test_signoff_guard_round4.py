"""Sprint 2 confirming review: bundled short flags, quote splicing, `cd rules`, hard links, the CI merge-base check."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_signoff_changes as csc

EDIT_HOOK = ROOT / ".claude/hooks/guard_rules.py"
BASH_HOOK = ROOT / ".claude/hooks/guard_signoff_bash.py"
BASE = "id: x\nstatus: draft\nreviewed_by: null\n"


def run_bash_hook(command):
    return subprocess.run([sys.executable, str(BASH_HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                          text=True, capture_output=True, check=False)


@pytest.mark.parametrize("command", [
    "git commit -nm x", "git commit -anm x", "git commit -a -n -m x", "git commit --no-\"verify\" -m x",
    "git -c 'core.hooks''Path=/dev/null' commit -m x", "git commit --no-ve -m x",
    "cd rules && sed -i 's/status: draft/status: approved/' ncc2022/a.yaml",
    "sed -i 's/status: draft/status: appr''oved/' rules/ncc2022/j6/a.yaml",
    "cd ncc2022/j6 && sed -i 's/reviewed_by: null/reviewed_by: Jane/' *.yaml",
])
def test_the_bash_hook_blocks_the_review_bypasses(command):
    assert run_bash_hook(command).returncode == 2, command


@pytest.mark.parametrize("command", ["git commit -m 'fix: n'", "git commit -am 'add tests'", "pytest -n 4 tests", "git log --no-verbose",
                                     "git status", "python scripts/validate_rules.py"])
def test_ordinary_commands_are_still_allowed(command):
    assert run_bash_hook(command).returncode == 0, command


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links unavailable")
def test_the_edit_hook_refuses_a_hard_linked_file(tmp_path):
    original = tmp_path / "x.txt"
    original.write_text(BASE, encoding="utf-8")
    alias = tmp_path / "alias.txt"
    try:
        os.link(original, alias)
    except OSError:
        pytest.skip("cannot create hard links here")
    r = subprocess.run([sys.executable, str(EDIT_HOOK)], text=True, capture_output=True, check=False, cwd=ROOT,
                       input=json.dumps({"tool_name": "Edit", "tool_input": {
                           "file_path": str(alias), "old_string": "draft", "new_string": "approved"}}))
    assert r.returncode == 2 and "hard links" in r.stderr


# ---- CI backstop: compare HEAD with the merge base ---------------------------------------------------------

def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def history(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    rule = tmp_path / "rules" / "ncc2025" / "j6" / "x.yaml"
    rule.parent.mkdir(parents=True)
    rule.write_text(BASE, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    monkeypatch.setattr(csc, "ROOT", tmp_path)
    return tmp_path, rule


def commit(repo, message):
    git(repo, "add", "-A")
    msg = repo.parent / f"{repo.name}-msg.txt"       # via a file: command-line encodings mangle invisible characters
    msg.write_bytes(message.encode("utf-8"))
    git(repo, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-F", str(msg))


def test_ci_fails_a_signed_rule_that_skipped_the_local_hook(history, capsys):
    repo, rule = history
    rule.write_text(BASE.replace("status: draft", "status: approved").replace("reviewed_by: null", "reviewed_by: Jane"),
                    encoding="utf-8")
    commit(repo, "sign it")
    assert csc.main(["--base", "main"]) == 1
    assert "sets reviewed_by" in capsys.readouterr().err


def test_ci_accepts_it_with_the_engineers_trailer(history):
    repo, rule = history
    rule.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    commit(repo, "sign it\n\nEngineer-Signoff: Jane Engineer RPEQ 12345")
    assert csc.main(["--base", "main"]) == 0


@pytest.mark.parametrize("trailer", ["Engineer-Signoff: x", "Engineer-Signoff:", "Engineer-Signoff: " + chr(0x200B) * 8])
def test_a_trivial_trailer_does_not_unlock_it(history, trailer):
    repo, rule = history
    rule.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    commit(repo, f"sign it\n\n{trailer}")
    assert csc.main(["--base", "main"]) == 1


def test_ci_ignores_unrelated_changes_and_a_rename_is_still_seen(history):
    repo, rule = history
    (repo / "notes.txt").write_text("hi", encoding="utf-8")
    commit(repo, "notes")
    assert csc.main(["--base", "main"]) == 0
    git(repo, "mv", str(rule), str(rule.with_name("y.yaml")))
    rule.with_name("y.yaml").write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    commit(repo, "move and sign")
    assert csc.main(["--base", "main"]) == 1


def test_an_unknown_base_fails_closed(history):
    assert csc.main(["--base", "no-such-branch"]) == 1
