"""The sign-off check compares with the newest *-gate or restore-* tag and fails closed without one."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_signoff_changes as csc
import release_gate as rg

BASE = "id: x\nstatus: draft\nreviewed_by: null\n"
SIGNED = BASE.replace("reviewed_by: null", "reviewed_by: Jane")


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)      # a scratch repo: it has no hooks


@pytest.fixture
def repo(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    rule = tmp_path / "rules" / "ncc2025" / "j6" / "x.yaml"
    rule.parent.mkdir(parents=True)
    rule.write_text(BASE, encoding="utf-8")
    commit(tmp_path, "base")
    monkeypatch.setattr(csc, "ROOT", tmp_path)
    return tmp_path, rule


def test_no_base_tag_fails_closed_with_a_clear_message(repo, capsys):
    assert csc.main(["--base-tag"]) == 1
    assert "no comparison base" in capsys.readouterr().err


@pytest.mark.parametrize("tag", ["restore-2026-10-09", "sprint-2-gate"])
def test_a_signed_change_after_the_base_tag_is_caught(repo, tag):
    root, rule = repo
    git(root, "tag", tag)
    rule.write_text(SIGNED, encoding="utf-8")
    commit(root, "sign it")
    assert csc.main(["--base-tag"]) == 1


def test_a_trailer_unlocks_it_and_other_tags_are_not_bases(repo):
    root, rule = repo
    git(root, "tag", "v1.0")
    assert csc.main(["--base-tag"]) == 1          # v1.0 is not a base
    git(root, "tag", "restore-2026-10-09")
    rule.write_text(SIGNED, encoding="utf-8")
    commit(root, "sign it\n\nEngineer-Signoff: Jane Engineer RPEQ 12345")
    assert csc.main(["--base-tag"]) == 0


def test_the_newest_base_tag_wins(repo):
    root, rule = repo
    git(root, "tag", "restore-2026-10-09")
    rule.write_text(SIGNED, encoding="utf-8")
    commit(root, "sign it, no declaration")
    assert csc.main(["--base-tag"]) == 1
    time.sleep(1.1)                                # creatordate has one-second resolution
    git(root, "tag", "-a", "-m", "gate", "sprint-2-gate")
    assert csc.base_tag() == "sprint-2-gate"
    assert csc.main(["--base-tag"]) == 0          # nothing changed since the newer base


@pytest.mark.parametrize("tag", ["sprint-2", "sprint-1-gate", "sprint-2-gate-restored", "restore-2026-10-09"])
def test_snapshot_and_recovery_tags_may_be_pushed(tag):
    assert rg.RELEASE_TAG_OK.match(tag)


@pytest.mark.parametrize("tag", ["restore-", "restore-today", "sprint-2-gate-x", "v1.0.0"])
def test_other_tags_still_may_not(tag):
    assert not rg.RELEASE_TAG_OK.match(tag)
