"""Sign-off protection: parse-based detection (not a text regex), Bash tripwire, commit tripwire."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_signoff_changes as csc
import signoff_guard as sg

EDIT_HOOK = ROOT / ".claude/hooks/guard_rules.py"
BASH_HOOK = ROOT / ".claude/hooks/guard_signoff_bash.py"
RULE = ROOT / "rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml"
BASE = "id: x\nstatus: draft\nreviewed_by: null\nchecked_by: null\nchecked_on: null\n"

BYPASSES = [  # every one of these slipped past the old line-start regex
    BASE.replace("checked_by: null", '"checked_by": Jane'),
    BASE.replace("checked_by: null", "'checked_by': Jane"),
    "id: x\nstatus: draft\n{reviewed_by: Jane}\n".replace("{reviewed_by: Jane}", "x: 1\nreviewed_by: {a: Jane}"),
    "id: x\nstatus: draft\n? reviewed_by\n: Jane\n",
    "base: &b {reviewed_by: Jane}\nid: x\nstatus: draft\n<<: *b\n",
    "﻿id: x\nstatus: draft\nreviewed_by: Jane\n",
    "id: x\nstatus: !!str approved\n",
    "id: x\nstatus: |\n  approved\n",
    'id: x\n"status": approved\n',
    "id: x\nstatus: draft\n---\nreviewed_on: 2026-10-07\n",
    "id: x\nstatus: draft\nreviewer_registration_no: ' 123 '\n",
    BASE.replace("reviewed_by: null", "reviewed_by: !!str Jane"),
    BASE.replace("checked_on: null", "checked_on: 2026-10-07"),
]
ALLOWED = [
    BASE, BASE.replace("reviewed_by: null", "reviewed_by: null # not yet"), BASE.replace("null", "NULL"),
    BASE.replace("reviewed_by: null", "reviewed_by: ''"), BASE.replace("reviewed_by: null", "reviewed_by: ~"),
    BASE.replace("id: x", "id: y"),
]


@pytest.mark.parametrize("new", BYPASSES)
def test_every_known_bypass_is_detected_when_a_file_changes(new):
    assert sg.violations(BASE, new), new


@pytest.mark.parametrize("new", ALLOWED)
def test_null_fields_and_unrelated_changes_are_allowed(new):
    assert sg.violations(BASE, new) == []


def test_a_value_already_in_the_file_is_not_flagged_again():
    signed = "id: x\nstatus: approved\nreviewed_by: E. Engineer\nreviewed_on: 2026-10-07\n"
    assert sg.violations(signed, signed.replace("id: x", "id: y")) == []
    assert sg.violations(signed, signed.replace("E. Engineer", "Someone Else"))


def test_unparseable_new_text_falls_back_to_a_text_scan_that_fails_closed():
    broken = "id: x\nreviewed_by: Jane\n  bad: [unclosed\n"
    assert sg.violations(BASE, broken)
    assert sg.violations(BASE, "id: x\nstatus: approved\n  : : [\n")


def test_invisible_values_do_not_count_as_a_change():
    assert sg.violations(BASE, BASE.replace("reviewed_by: null", f"reviewed_by: '{chr(0x200B)}'")) == []


@pytest.mark.parametrize("path", ["/r/rules/ncc2025/j6/x.yaml", "rules/ncc2025/j6/x.yaml", "RULES\\ncc2025\\j6\\X.YAML",
                                  "/r/rules/ncc2022/j6/x.yml"])
def test_rule_paths_are_recognised_relative_absolute_and_case_insensitively(path):
    assert sg.is_rule_path(path)


@pytest.mark.parametrize("path", ["/r/rules/schema/rule.schema.json", "/r/rules/adoption.yaml",
                                  "/r/rules/applicability.yaml", "/r/docs/x.yaml", "/r/apps/rules.py"])
def test_other_files_are_not_rule_paths(path):
    assert not sg.is_rule_path(path)


def test_apply_edits_handles_edit_multiedit_and_replace_all():
    assert sg.apply_edits("a b a", {"old_string": "a", "new_string": "c"}) == "c b a"
    assert sg.apply_edits("a b a", {"old_string": "a", "new_string": "c", "replace_all": True}) == "c b c"
    assert sg.apply_edits("a b", {"edits": [{"old_string": "a", "new_string": "x"}, {"old_string": "b", "new_string": "y"}]}) == "x y"


def run_edit_hook(tool, tool_input):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(EDIT_HOOK)], input=payload, text=True, capture_output=True,
                          check=False, cwd=ROOT)


def test_the_edit_hook_blocks_a_partial_edit_that_turns_the_rule_approved():
    r = run_edit_hook("Edit", {"file_path": str(RULE), "old_string": "status: draft", "new_string": "status: approved"})
    assert r.returncode == 2 and "approved" in r.stderr


def test_the_edit_hook_blocks_a_partial_edit_that_fills_a_field_with_quotes():
    r = run_edit_hook("Edit", {"file_path": str(RULE), "old_string": "reviewed_by: null",
                               "new_string": '"checked_by": "J. Smith"\nreviewed_by: null'})
    assert r.returncode == 2


def test_the_edit_hook_blocks_a_relative_path_and_an_upper_case_extension(tmp_path):
    for path in ("rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml", "RULES/NCC2025/J6/X.YAML"):
        r = run_edit_hook("Write", {"file_path": path, "content": "id: x\nreviewed_by: Jane\n"})
        assert r.returncode == 2, path


def test_the_edit_hook_allows_ordinary_rule_edits_and_null_sign_off_fields():
    ok = run_edit_hook("Edit", {"file_path": str(RULE), "old_string": "near_miss: 0.05", "new_string": "near_miss: 0.04"})
    assert ok.returncode == 0
    ok = run_edit_hook("Write", {"file_path": "/r/rules/ncc2025/j6/x.yaml", "content": BASE})
    assert ok.returncode == 0


def run_bash_hook(command):
    return subprocess.run([sys.executable, str(BASH_HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                          text=True, capture_output=True, check=False)


@pytest.mark.parametrize("command", [
    "sed -i 's/reviewed_by: null/reviewed_by: Jane/' rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml",
    "python -c \"import yaml;...open('rules/ncc2022/j6/a.yaml','w').write('status: approved')\"",
    "echo 'checked_by: Jane' >> rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml",
    "cat <<EOF > rules/ncc2025/j6/x.yaml\nstatus: approved\nEOF",
    "perl -pi -e 's/draft/approved/' rules/ncc2025/j6/*.yaml",
    "tee rules/ncc2025/j6/x.yaml <<< 'reviewer_registration_no: 1'",
    'MEP_HUMAN_SIGNOFF="Engineer RPEQ 1" git commit -am x',
])
def test_the_bash_hook_blocks_sign_off_written_from_the_shell(command):
    assert run_bash_hook(command).returncode == 2


@pytest.mark.parametrize("command", ["python scripts/validate_rules.py", "pytest tests/rules", "git status",
                                     "cat rules/ncc2025/j6/NCC2025-J6D3-deadband.yaml", "ls rules"])
def test_the_bash_hook_allows_ordinary_commands(command):
    assert run_bash_hook(command).returncode == 0


# ---- the commit tripwire --------------------------------------------------------------------------

def git(repo, *args, env=None):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True, env=env)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    rule = tmp_path / "rules" / "ncc2025" / "j6" / "x.yaml"
    rule.parent.mkdir(parents=True)
    rule.write_text(BASE, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "base")
    monkeypatch.setattr(csc, "ROOT", tmp_path)
    return tmp_path, rule


def test_the_tripwire_stops_a_commit_that_fills_sign_off_fields(repo, monkeypatch, capsys):
    path, rule = repo
    rule.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    git(path, "add", "-A")
    monkeypatch.delenv("MEP_HUMAN_SIGNOFF", raising=False)
    assert csc.main() == 1
    assert "sets reviewed_by" in capsys.readouterr().err


def test_the_tripwire_allows_it_when_the_engineer_declares_who_is_signing(repo, monkeypatch, capsys):
    path, rule = repo
    rule.write_text(BASE.replace("status: draft", "status: approved").replace("reviewed_by: null", "reviewed_by: Jane"),
                    encoding="utf-8")
    git(path, "add", "-A")
    monkeypatch.setenv("MEP_HUMAN_SIGNOFF", "Jane Engineer RPEQ 12345")
    assert csc.main() == 0
    assert "declared by 'Jane Engineer RPEQ 12345'" in capsys.readouterr().err


def test_the_tripwire_ignores_unrelated_staged_changes(repo, monkeypatch):
    path, rule = repo
    rule.write_text(BASE + "notes: hello\n", encoding="utf-8")
    git(path, "add", "-A")
    monkeypatch.delenv("MEP_HUMAN_SIGNOFF", raising=False)
    assert csc.main() == 0


def test_a_too_short_declaration_does_not_unlock_the_tripwire(repo, monkeypatch):
    path, rule = repo
    rule.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    git(path, "add", "-A")
    monkeypatch.setenv("MEP_HUMAN_SIGNOFF", "x")
    assert csc.main() == 1
