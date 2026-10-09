"""The hooks run every check inside the devcontainer; if it cannot be reached the stop gate blocks instead of passing."""
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[2] / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))
import devcontainer_exec as dc


def cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def load_stop_gate():
    spec = importlib.util.spec_from_file_location("stop_gate_under_test", HOOKS / "stop_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("a, b", [
    ("C:\\Users\\G\\MEP_AI", "c:/users/g/mep_ai/"),
    ("c:\\Users\\G\\MEP_AI\\", "C:/Users/G/MEP_AI"),
    ("/home/me/proj", "/home/me/proj/"),
])
def test_paths_compare_across_host_spellings(a, b):
    assert dc.norm(a) == dc.norm(b)


def test_a_host_file_maps_into_the_container_workspace():
    ws = "/workspaces/MEP_AI"
    assert dc.to_container_path("C:\\Users\\G\\MEP_AI", "C:\\Users\\G\\MEP_AI\\rules\\ncc2025\\x.yaml", ws) == \
        "/workspaces/MEP_AI/rules/ncc2025/x.yaml"
    assert dc.to_container_path("c:/Users/G/MEP_AI", "C:\\Users\\G\\MEP_AI\\scripts\\a.py", ws) == \
        "/workspaces/MEP_AI/scripts/a.py"
    assert dc.to_container_path("C:\\Users\\G\\MEP_AI", "C:\\other\\a.py", ws) == "C:\\other\\a.py"


def test_the_container_is_found_by_its_folder_label_and_started_when_stopped(monkeypatch):
    calls = []

    def fake(*args, timeout=60):
        calls.append(args)
        if args[0] == "ps":
            return cp(stdout="aaa\trunning\tc:\\Users\\G\\Other\nbbb\texited\tc:\\Users\\G\\MEP_AI\n")
        return cp()

    monkeypatch.setattr(dc, "_docker", fake)
    assert dc.find_container("C:/Users/G/MEP_AI") == ("bbb", "/workspaces/MEP_AI")
    assert ("start", "bbb") in [c[:2] for c in calls]


def test_no_matching_container_or_a_dead_docker_is_not_found(monkeypatch):
    monkeypatch.setattr(dc, "_docker", lambda *a, timeout=60: cp(stdout="aaa\trunning\tc:\\Users\\G\\Other\n"))
    assert dc.find_container("C:/Users/G/MEP_AI") is None
    monkeypatch.setattr(dc, "_docker", lambda *a, timeout=60: cp(returncode=1, stderr="daemon not running"))
    assert dc.find_container("C:/Users/G/MEP_AI") is None


def test_run_reports_unreachable_with_a_clear_message(monkeypatch):
    monkeypatch.setattr(dc, "find_container", lambda root: None)
    r = dc.run("true", "C:/Users/G/MEP_AI")
    assert r.returncode == dc.UNREACHABLE and "devcontainer" in r.stderr
    def boom(root):
        raise FileNotFoundError("docker")
    monkeypatch.setattr(dc, "find_container", boom)
    assert dc.run("true", "C:/x").returncode == dc.UNREACHABLE


def run_gate(monkeypatch, capsys, result):
    gate = load_stop_gate()
    monkeypatch.setattr(gate.devcontainer_exec, "run", lambda command, root, timeout=540: result)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(HOOKS.parents[1]))
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    with pytest.raises(SystemExit):
        gate.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_the_stop_gate_blocks_when_the_container_cannot_be_reached(monkeypatch, capsys):
    verdict = run_gate(monkeypatch, capsys, cp(dc.UNREACHABLE, "", "no running devcontainer"))
    assert verdict["decision"] == "block" and "could not run" in verdict["reason"]


def test_the_stop_gate_blocks_on_failing_tests_and_passes_on_green(monkeypatch, capsys):
    verdict = run_gate(monkeypatch, capsys, cp(1, "FAILED tests/rules/x.py::test", ""))
    assert verdict["decision"] == "block" and "failing" in verdict["reason"] and "FAILED" in verdict["reason"]
    assert run_gate(monkeypatch, capsys, cp(0, "1 passed", "")) is None
    assert run_gate(monkeypatch, capsys, cp(5, "", "")) is None          # no tests collected


def test_the_stop_gate_never_runs_tests_on_the_host():
    source = (HOOKS / "stop_gate.py").read_text(encoding="utf-8")
    assert "sys.executable" not in source and "shutil.which" not in source
    assert "devcontainer_exec.run(" in source
