"""Sign-off guard, review round 2: deletion-only edits, unmatched edits, path aliases, renames, hook bypass."""
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


def run_edit_hook(tool, tool_input):
    payload = json.dumps({"tool_name": tool, "tool_input": tool_input})
    return subprocess.run([sys.executable, str(EDIT_HOOK)], input=payload, text=True, capture_output=True,
                          check=False, cwd=ROOT)


def run_bash_hook(command):
    return subprocess.run([sys.executable, str(BASH_HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                          text=True, capture_output=True, check=False)


def test_a_deletion_only_edit_that_activates_a_commented_value_is_a_violation():
    text = "id: x\nstatus: draft\n#reviewed_by: Jane\n"
    assert sg.violations(text, sg.apply_edits(text, {"old_string": "#", "new_string": ""}))


def test_the_edit_hook_checks_even_when_new_string_is_empty(tmp_path):
    f = tmp_path / "rules" / "ncc2025" / "j6" / "x.yaml"
    f.parent.mkdir(parents=True)
    f.write_text("id: x\nstatus: draft\n#reviewed_by: Jane\n", encoding="utf-8")
    r = run_edit_hook("Edit", {"file_path": str(f), "old_string": "#", "new_string": ""})
    assert r.returncode == 2


def test_an_edit_whose_old_string_is_not_in_the_file_is_refused_not_waved_through():
    with pytest.raises(sg.EditDoesNotMatch):
        sg.apply_edits("a b", {"old_string": "zzz", "new_string": "reviewed_by: Jane"})
    r = run_edit_hook("Edit", {"file_path": str(RULE), "old_string": "no such text anywhere", "new_string": "x"})
    assert r.returncode == 2


@pytest.mark.parametrize("path", ["/r/rules/ncc2025/j6/x.yaml.", "/r/rules/ncc2025/j6/x.yaml ", "/r/RULES/./ncc2025/j6/x.yaml",
                                  "/r/rules/ncc2025/j6/x.yaml::$DATA", "/r/rules/ADOPTION.yaml", "/r/rules/Schema/x.yaml"])
def test_windows_aliases_and_case_variants_of_a_rule_path_are_still_guarded(path):
    assert sg.is_rule_path(path)


def test_the_exact_adoption_and_schema_files_are_not_rules():
    assert not sg.is_rule_path("/r/rules/adoption.yaml") and not sg.is_rule_path("/r/rules/schema/x.yaml")


@pytest.mark.parametrize("command", ["git commit --no-verify -m x", "git -c core.hooksPath=/dev/null commit -m x",
                                     "git config core.hooksPath /tmp/none", "git commit -n -m x", "git commit -an -m x"])
def test_the_bash_hook_blocks_skipping_git_hooks(command):
    assert run_bash_hook(command).returncode == 2


def test_the_bash_hook_still_allows_a_plain_commit():
    assert run_bash_hook("git commit -m 'Add tests'").returncode == 0


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def test_the_tripwire_sees_a_rename_that_also_fills_a_field(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    rule = tmp_path / "rules" / "ncc2025" / "j6" / "x.yaml"
    rule.parent.mkdir(parents=True)
    rule.write_text(BASE, encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "core.hooksPath=/dev/null", "commit", "-q", "-m", "base")
    monkeypatch.setattr(csc, "ROOT", tmp_path)
    new = rule.with_name("y.yaml")
    git(tmp_path, "mv", str(rule), str(new))
    new.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    git(tmp_path, "add", "-A")
    monkeypatch.delenv("MEP_HUMAN_SIGNOFF", raising=False)
    assert csc.main() == 1


def test_a_golden_meta_cannot_be_made_real_by_an_agent():
    assert sg.is_golden_real_path("evals/golden/pilot-1/meta.yaml")
    assert sg.is_golden_real_path("evals/golden/p/data_agreement.pdf")
    assert sg.golden_real_change("synthetic: true\nname: p\n", "synthetic: false\nname: p\n")
    assert not sg.golden_real_change("synthetic: true\nname: p\n", "synthetic: true\nname: p\nnote: x\n")
