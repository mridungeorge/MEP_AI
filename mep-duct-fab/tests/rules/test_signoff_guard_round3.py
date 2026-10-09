"""Sprint 2 review round: empty old_string creation, golden proof files from the shell, abbreviated git options."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EDIT_HOOK = ROOT / ".claude/hooks/guard_rules.py"
BASH_HOOK = ROOT / ".claude/hooks/guard_signoff_bash.py"
BASE = "id: x\nstatus: draft\nreviewed_by: null\nchecked_by: null\nchecked_on: null\n"
SIGNED = "id: x\nstatus: approved\nreviewed_by: Agent\nreviewed_on: 2026-01-01\n"


def run_edit_hook(tool_input):
    return subprocess.run([sys.executable, str(EDIT_HOOK)], input=json.dumps({"tool_name": "Edit", "tool_input": tool_input}),
                          text=True, capture_output=True, check=False, cwd=ROOT)


def run_bash_hook(command):
    return subprocess.run([sys.executable, str(BASH_HOOK)], input=json.dumps({"tool_input": {"command": command}}),
                          text=True, capture_output=True, check=False)


def test_an_edit_with_an_empty_old_string_cannot_create_a_signed_rule(tmp_path):
    target = tmp_path / "rules" / "ncc2022" / "j6" / "NCC2022-J6D3-zz-new.yaml"
    target.parent.mkdir(parents=True)
    assert run_edit_hook({"file_path": str(target), "old_string": "", "new_string": SIGNED}).returncode == 2
    assert run_edit_hook({"file_path": str(target), "old_string": "", "new_string": BASE}).returncode == 0
    target.write_text(BASE, encoding="utf-8")        # an existing file cannot be 'edited' with an empty old_string
    assert run_edit_hook({"file_path": str(target), "old_string": "", "new_string": SIGNED}).returncode == 2


def test_an_empty_old_string_creating_a_golden_meta_is_judged(tmp_path):
    meta = tmp_path / "evals" / "golden" / "pilot-1" / "meta.yaml"
    meta.parent.mkdir(parents=True)
    r = run_edit_hook({"file_path": str(meta), "old_string": "",
                       "new_string": "name: pilot-1\nsynthetic: false\nfirm: X Pty Ltd\n"})
    assert r.returncode == 2


@pytest.mark.parametrize("command", [
    "echo signed > evals/golden/pilot-1/data_agreement.txt",
    "python -c \"open('evals/golden/p/meta.yaml','w').write('synthetic: false')\"",
    "cp x.pdf evals/golden/pilot/data_agreement.pdf",
    "git commit --no-veri -m x",
])
def test_the_bash_hook_blocks_golden_proof_files_and_abbreviated_skip_options(command):
    assert run_bash_hook(command).returncode == 2


def test_the_bash_hook_still_allows_reading_golden_files():
    assert run_bash_hook("ls evals/golden").returncode == 0
