"""Review round 3: a rule path stored as a symbolic link can point at an unguarded file; trailers are per file."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_signoff_changes as csc

BASE = "id: x\nstatus: draft\nreviewed_by: null\n"
NO_HOOKS = ["-c", "core.hooksPath=/dev/null"]


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
    git(tmp_path, *NO_HOOKS, "commit", "-q", "-m", "base")
    git(tmp_path, "checkout", "-q", "-b", "feature")
    monkeypatch.setattr(csc, "ROOT", tmp_path)
    return tmp_path, rule


def commit(repo, message):
    git(repo, "add", "-A")
    msg = repo.parent / f"{repo.name}-msg.txt"
    msg.write_bytes(message.encode("utf-8"))
    git(repo, *NO_HOOKS, "commit", "-q", "-F", str(msg))


def stage_symlink(repo, rel, target_text):
    blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo, input=target_text, text=True,
                          capture_output=True, check=True).stdout.strip()
    git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},{rel}")


def test_ci_refuses_a_symbolic_link_under_rules(history, capsys):
    repo, _ = history
    stage_symlink(repo, "rules/ncc2025/j6/linked.yaml", "../../../docs/notes/x.yaml")
    git(repo, *NO_HOOKS, "commit", "-q", "-m", "link")
    assert csc.main(["--base", "main"]) == 1
    assert "symbolic links" in capsys.readouterr().err


def test_the_staged_check_refuses_a_symbolic_link_under_rules(history, capsys):
    repo, _ = history
    stage_symlink(repo, "rules/ncc2025/j6/linked.yaml", "../../../docs/notes/x.yaml")
    assert csc.main([]) == 1
    assert "symbolic links" in capsys.readouterr().err


def test_a_trailer_on_another_commit_does_not_cover_a_different_file(history):
    repo, rule = history
    other = rule.with_name("y.yaml")
    other.write_text(BASE, encoding="utf-8")
    commit(repo, "add y")
    rule.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Jane"), encoding="utf-8")
    commit(repo, "engineer signs x\n\nEngineer-Signoff: Jane Engineer RPEQ 12345")
    assert csc.main(["--base", "main"]) == 0
    other.write_text(BASE.replace("reviewed_by: null", "reviewed_by: Agent"), encoding="utf-8")
    commit(repo, "agent signs y")
    assert csc.main(["--base", "main"]) == 1


def test_the_loader_refuses_a_symlinked_rule_file(tmp_path):
    from mep.engine.loader import RuleLoadError, load_pack

    rules = tmp_path / "rules"
    (rules / "schema").mkdir(parents=True)
    (rules / "ncc2025").mkdir()
    (rules / "schema" / "rule.schema.json").write_text(
        (ROOT / "rules" / "schema" / "rule.schema.json").read_text(encoding="utf-8"), encoding="utf-8")
    target = tmp_path / "elsewhere.yaml"
    target.write_text(BASE, encoding="utf-8")
    try:
        (rules / "ncc2025" / "x.yaml").symlink_to(target)
    except OSError:
        pytest.skip("cannot create symlinks here")
    with pytest.raises(RuleLoadError):
        load_pack(rules)
