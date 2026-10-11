import importlib.util
import os
import subprocess
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("changelog", Path(__file__).parents[2] / "scripts" / "changelog.py")
assert SPEC and SPEC.loader
changelog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(changelog)


def git(repo: Path, *args: str, date: str = "2026-01-01T00:00:00") -> None:
    env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com", "PATH": os.environ["PATH"],
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
    subprocess.run(["git", "-c", "tag.gpgsign=false", "-c", "commit.gpgsign=false", *args], cwd=repo, check=True, capture_output=True, env=env)


def commit(repo: Path, msg: str, date: str) -> None:
    (repo / "f.txt").write_text(msg, encoding="utf-8")
    git(repo, "add", "f.txt")
    git(repo, "commit", "-m", msg, date=date)


def make_repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    commit(tmp_path, "first work", "2026-01-01T00:00:00")
    git(tmp_path, "tag", "-a", "sprint-1-gate", "-m", "g1", date="2026-01-02T00:00:00")
    commit(tmp_path, "second work", "2026-02-01T00:00:00")
    commit(tmp_path, "third work", "2026-02-02T00:00:00")
    git(tmp_path, "tag", "-a", "sprint-2-gate", "-m", "g2", date="2026-02-03T00:00:00")
    git(tmp_path, "tag", "-a", "other-tag", "-m", "x", date="2026-02-03T00:00:00")
    commit(tmp_path, "after the gate", "2026-03-01T00:00:00")
    return tmp_path


def test_sections_and_order(tmp_path: Path) -> None:
    text = changelog.build(make_repo(tmp_path))
    assert text.index("## Unreleased") < text.index("## sprint-2-gate (2026-02-03)") < text.index("## sprint-1-gate (2026-01-02)")
    unreleased = text.split("## sprint-2-gate")[0]
    assert "- after the gate" in unreleased and "second work" not in unreleased
    mid = text.split("## sprint-2-gate")[1].split("## sprint-1-gate")[0]
    assert "- second work" in mid and "- third work" in mid and "first work" not in mid
    assert "- first work" in text.split("## sprint-1-gate")[1]
    assert "other-tag" not in text


def test_deterministic_and_cli(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    assert changelog.build(repo) == changelog.build(repo)
    out = tmp_path / "out.md"
    subprocess.run([sys.executable, str(Path(changelog.__file__)), "--repo", str(repo), "--out", str(out)], check=True)
    assert out.read_text(encoding="utf-8") == changelog.build(repo)


def test_no_tags(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    commit(tmp_path, "only", "2026-01-01T00:00:00")
    assert "- only" in changelog.build(tmp_path)
