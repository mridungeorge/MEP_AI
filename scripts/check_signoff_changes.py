#!/usr/bin/env python3
"""Tripwire: a change to a rule's sign-off or check fields (or to status: approved) must be a deliberate human act.

  python scripts/check_signoff_changes.py              compare STAGED rule files with HEAD (.githooks/pre-commit)
  python scripts/check_signoff_changes.py --base REF   compare HEAD with the merge base of REF (CI backstop: this still
                                                       runs when a local hook was skipped)
  MEP_HUMAN_SIGNOFF="E. Engineer RPEQ 12345" ...       the engineer's own shell declares who is signing (staged mode)
  Engineer-Signoff: E. Engineer RPEQ 12345             the same declaration as a commit-message trailer (--base mode)

The Bash hook blocks agents from setting MEP_HUMAN_SIGNOFF. This is a tripwire for honest mistakes and for agents, not a
lock: a person can skip local hooks, and an agent can also write the trailer. What the CI mode adds is that a sign-off
change cannot reach the main branch without a declaration that a reviewer can see in the history.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import signoff_guard

TRAILER = "Engineer-Signoff:"


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, cwd=ROOT, check=False)
    try:
        return out.stdout.decode("utf-8")
    except UnicodeDecodeError:       # undecodable bytes cannot be judged: fail closed (the callers treat this as a change)
        raise SystemExit(f"check_signoff_changes: git output for {' '.join(args)[:60]} is not valid UTF-8; refusing") from None


def _is_rule(name: str) -> bool:
    return any(signoff_guard.is_rule_path(p) for p in signoff_guard.real_paths(name))


def staged_rule_files() -> list[str]:
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMRT").splitlines()
    return [n for n in names if _is_rule(n)]


def declared_by(trailer_lines: list[str]) -> str:
    for line in trailer_lines:
        if line.startswith(TRAILER):
            who = signoff_guard.visible(line[len(TRAILER):])
            if len(who) >= 5:
                return who
    return ""


def _report(problems: list[str], who: str, hint: str) -> int:
    if not problems:
        return 0
    if who:
        print(f"check_signoff_changes: sign-off change declared by {who!r}:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 0
    print("check_signoff_changes: this change alters human sign-off fields:", file=sys.stderr)
    for p in problems:
        print(f"  {p}", file=sys.stderr)
    print(hint, file=sys.stderr)
    return 1


def symlinks_under_rules(*ls_args: str) -> list[str]:
    """Paths under rules/ stored as symbolic links (git mode 120000): a link can point at an unguarded file."""
    out = git(*ls_args, "--", "rules")
    names = []
    for line in out.splitlines():
        meta, _, name = line.partition("\t")
        if meta.split()[0] == "120000":
            names.append(name)
    return names


def staged() -> int:
    links = symlinks_under_rules("ls-files", "-s")
    if links:
        print("check_signoff_changes: symbolic links under rules/ are not allowed: " + ", ".join(links), file=sys.stderr)
        return 1
    problems: list[str] = []
    for name in staged_rule_files():
        old = git("show", f"HEAD:{name}")
        new = git("show", f":{name}")
        problems += [f"{name}: {why}" for why in signoff_guard.violations(old, new)]
    who = signoff_guard.visible(os.environ.get("MEP_HUMAN_SIGNOFF"))
    return _report(problems, who if len(who) >= 5 else "",
                   "Only the engineer does this. In your own shell: MEP_HUMAN_SIGNOFF=\"Name RPEQ 12345\" git commit ...")


def since_base(ref: str) -> int:
    base = git("merge-base", ref, "HEAD").strip()
    if not base:
        print(f"check_signoff_changes: no merge base with {ref!r}; cannot compare", file=sys.stderr)
        return 1
    links = symlinks_under_rules("ls-tree", "-r", "HEAD")
    if links:
        print("check_signoff_changes: symbolic links under rules/ are not allowed: " + ", ".join(links), file=sys.stderr)
        return 1
    names = git("diff", "--name-only", "--diff-filter=ACMRT", f"{base}..HEAD").splitlines()
    problems: list[str] = []
    declared: list[str] = []
    for name in names:
        if not _is_rule(name):
            continue
        old = git("show", f"{base}:{name}")
        new = git("show", f"HEAD:{name}")
        problems += [f"{name}: {why}" for why in signoff_guard.violations(old, new)]
        if problems and problems[-1].startswith(f"{name}:"):
            trailers = [line.strip() for line in git("log", "--format=%B", f"{base}..HEAD", "--", name).splitlines()]
            if declared_by(trailers):
                problems = [p for p in problems if not p.startswith(f"{name}:")]
                declared.append(f"{name}: {declared_by(trailers)}")
    if declared:
        print("check_signoff_changes: sign-off declared for " + "; ".join(declared), file=sys.stderr)
    return _report(problems, "",
                   f"Add a commit-message trailer `{TRAILER} Name RPEQ 12345` written by the engineer who signs.")


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args[:1] == ["--base"] and len(args) == 2:
        return since_base(args[1])
    if args:
        print("usage: check_signoff_changes.py [--base REF]", file=sys.stderr)
        return 2
    return staged()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
