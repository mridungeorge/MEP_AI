#!/usr/bin/env python3
"""Block any release claim while there are zero REAL (non-synthetic) golden projects.

A golden project is real only when its meta.yaml says `synthetic: false` AND names a pilot `firm`, a
`data_agreement` (the id of an agreement file that exists in the project folder, `data_agreement.*`, non-empty) and the
engineer who signed it off with a registration number (`engineer_signed_off_by`). Placeholder text (TBD, N/A, x ...) and
invisible characters do not count. Everything else is synthetic and proves the engine, not the product.

  python scripts/release_gate.py --labels   every golden project has a valid meta.yaml (CI runs this)
  python scripts/release_gate.py --scan     fail if docs, READMEs or version fields make a release claim while no real
                                            project exists (CI runs this)
  python scripts/release_gate.py --check    fail while zero real golden projects exist (CI runs it on non-sprint tags and
                                            on GitHub releases; run it before tagging a release)
  python scripts/release_gate.py --message "text"   claim scan on one commit message (.githooks/commit-msg)
  python scripts/release_gate.py --tag name         tag check (.githooks/pre-push): only `sprint-N` tags may be pushed
                                                    while zero real golden projects exist

Limits (be honest about them): local git hooks can be skipped with --no-verify, a GitHub release can be created from the
web UI, and a clone without `core.hooksPath` has no hooks. That is why CI also runs --scan on every push and --check on
every non-sprint tag and on every published GitHub release: a claim made another way still shows up red.
"""
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "evals" / "golden"
REAL_FIELDS = ("firm", "data_agreement", "engineer_signed_off_by")
PLACEHOLDERS = {"tbd", "tba", "n/a", "na", "none", "null", "nil", "x", "xx", "xxx", "-", "--", "?", "todo", "unknown",
                "test", "example", "fake", "dummy", "placeholder", "lorem", "pending", "later", "firm", "name",
                "engineer", "agreement", "true", "false", "yes", "no", "0", "1"}

# Phrases that claim the product is released, ready or verified. The scan ignores a claim only when a negation word
# sits in the few words BEFORE it (so "Production-ready. No known bugs." is still a claim).
CLAIM = re.compile(
    r"""(?ix)\b(
        production[\s-]*ready | prod[\s-]*ready | pilot[\s-]*ready | release[\s-]*ready | customer[\s-]*ready |
        ready\s+(?:for|to)\s+(?:release|pilot|production|customers|ship|launch|go\s*live|general\s+use) |
        release\s+candidate | rc\s?\d+ | generally[\s-]*available | \bGA\b |
        (?:has\s+been|have\s+been|is|are|was|now|we\s+have|just|officially)\s+(?:released|shipped|launched) |
        (?:released|shipped|launched)\s+(?:to|v\d|version|as|on) |
        (?:v|version\s+)\d+\.\d+(?:\.\d+)?\s+(?:released|shipped|is\s+out) |
        now\s+in\s+production | (?:is|are)\s+live |
        (?:is|are)\s+(?:engineer[\s-]*)?verified | verified\s+by\s+(?:an?\s+)?engineers? | engineer[\s-]*verified |
        compliance[\s-]*verified | certified\s+compliant | fully\s+compliant | (?:is|are)\s+compliant
    )\b"""
)
NEGATION = re.compile(r"(?i)\b(not|no|never|until|before|cannot|can't|isn't|aren't|without|nor|neither|zero)\b")
QUOTED = re.compile(r"""(?s)(`[^`]*`|"[^"]*"|'[^']*')""")
RELEASE_TAG_OK = re.compile(r"^sprint-\d+$")
SKIP_DIRS = {"node_modules", ".git", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache",
             ".hypothesis", "supabase", "tests", ".githooks"}
SKIP_FILES = {"BUILD_PROMPT.md", "CLAUDE.md", "release_gate.py", "pnpm-lock.yaml", "package-lock.json"}
SCAN_SUFFIXES = {".md", ".txt", ".rst", ".html", ".yml", ".yaml", ".json", ".toml"}


def normalise(text: str) -> str:
    """Fold look-alike characters (non-breaking hyphen/space, full-width letters) before matching."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(" " if unicodedata.category(c) == "Zs" else ("-" if unicodedata.category(c) == "Pd" else c)
                   for c in text if unicodedata.category(c) != "Cf")


def projects() -> list[Path]:
    return sorted(p for p in GOLDEN.iterdir() if (p / "project.yaml").exists())


def load_meta(directory: Path) -> dict[str, Any]:
    path = directory / "meta.yaml"
    if not path.exists():
        raise ValueError(f"{directory.name}: meta.yaml is missing")
    meta = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or type(meta.get("synthetic")) is not bool:
        raise ValueError(f"{directory.name}: meta.yaml needs `synthetic: true|false`")
    return meta


def real_value(value: Any) -> str:
    """The field as plain visible text, or '' if it is a placeholder, too short, has no letters, or is not text."""
    if type(value) is not str:
        return ""
    text = "".join(c for c in unicodedata.normalize("NFKC", value) if unicodedata.category(c) not in ("Cc", "Cf")).strip()
    if len(text) < 4 or text.lower() in PLACEHOLDERS or not any(c.isalpha() for c in text):
        return ""
    return text


def agreement_file(directory: Path, agreement_id: str) -> Path | None:
    """The data agreement must exist as a non-empty file `data_agreement.*` in the project folder."""
    for f in sorted(directory.glob("data_agreement.*")):
        if f.is_file() and f.stat().st_size > 0:
            return f
    return None


def is_real(meta: dict[str, Any], directory: Path | None = None) -> bool:
    """Real = explicitly not synthetic, firm and signing engineer named in real text (the engineer with a
    registration number: it contains a digit), and the agreement exists as a file in the project folder."""
    if meta["synthetic"] is not False:
        return False
    firm, agreement, engineer = (real_value(meta.get(f)) for f in REAL_FIELDS)
    if not (firm and agreement and engineer) or not any(c.isdigit() for c in engineer):
        return False
    if directory is None or directory.name.lower().startswith("syn-") or meta.get("name") != directory.name:
        return False
    return agreement_file(directory, agreement) is not None


def label(directory: Path) -> str:
    return "REAL" if is_real(load_meta(directory), directory) else "SYNTHETIC"


def counts() -> tuple[int, int]:
    metas = [(load_meta(p), p) for p in projects()]
    real = sum(is_real(m, p) for m, p in metas)
    return len(metas) - real, real


def check_labels() -> list[str]:
    problems = []
    for p in projects():
        try:
            meta = load_meta(p)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        named = any(str(meta.get(f) or "").strip() for f in REAL_FIELDS)
        if meta["synthetic"] is False and not is_real(meta, p):
            problems.append(
                f"{p.name}: synthetic: false but it is not a real project: it needs a real firm name, an engineer with a "
                "registration number, a data_agreement.* file in the folder, name == folder name and no syn- prefix")
        if meta["synthetic"] is True and named:
            problems.append(f"{p.name}: synthetic: true but names a firm/agreement: pick one")
        if meta["synthetic"] is True and not p.name.startswith("syn-"):
            problems.append(f"{p.name}: synthetic projects must be named syn-*")
        if meta.get("name") != p.name:
            problems.append(f"{p.name}: meta.yaml name must equal the folder name")
    return problems


def claims_in(text: str) -> list[str]:
    """Release claims in `text`. A claim is ignored only if it is quoted or a negation word appears in the few words
    before it ('not production-ready'); a negation elsewhere on the line ('Production-ready. No known bugs.') does not hide it."""
    found = []
    for raw in text.splitlines():
        line = QUOTED.sub(" ", normalise(raw))
        for m in CLAIM.finditer(line):
            before = line[: m.start()].split()[-6:]
            if not NEGATION.search(" ".join(before)):
                found.append(raw.strip()[:160])
                break
    return found


def scan_paths() -> list[Path]:
    out = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.name in SKIP_FILES or path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        if rel.parts[0] == "rules" or rel.parts[:2] == ("evals", "dual_encoding") or rel.parts[:2] == ("docs", "engineer-review"):
            continue  # rule data, second-pass extraction and generated review sheets quote the ABCB, not our status
        out.append(path)
    return out


def version_claims() -> list[str]:
    """A version above 0.0.0 in package.json / pyproject.toml is itself a release claim."""
    out = []
    for name, pattern in (("package.json", r'"version"\s*:\s*"([^"]+)"'), ("apps/web/package.json", r'"version"\s*:\s*"([^"]+)"'),
                          ("pyproject.toml", r'(?m)^version\s*=\s*"([^"]+)"')):
        path = ROOT / name
        if path.exists():
            m = re.search(pattern, path.read_text(encoding="utf-8"))
            if m and m.group(1) != "0.0.0":
                out.append(f"{name}: version {m.group(1)} (must stay 0.0.0 while there are no real golden projects)")
    return out


def scan() -> list[str]:
    _synthetic, real = counts()
    if real > 0:
        return []
    out = version_claims()
    for path in scan_paths():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        out += [f"{path.relative_to(ROOT).as_posix()}: {line}" for line in claims_in(text)]
    return out


def blocked(summary: str) -> int:
    print(f"release_gate: BLOCKED. {summary}. No release claim is allowed while there are zero real (non-synthetic) "
          "golden projects. Synthetic projects prove the engine, not the product.", file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    synthetic, real = counts()
    summary = f"golden projects: {synthetic} SYNTHETIC, {real} REAL"
    if "--labels" in argv:
        problems = check_labels()
        print(summary)
        for p in problems:
            print(f"release_gate: {p}", file=sys.stderr)
        return 1 if problems else 0
    if "--scan" in argv:
        claims = scan()
        print(summary)
        for c in claims:
            print(f"release_gate: release claim while there are no real golden projects: {c}", file=sys.stderr)
        return 1 if claims else 0
    if "--message" in argv:
        text = argv[argv.index("--message") + 1]
        return blocked(summary) if real == 0 and claims_in(text) else 0
    if "--tag" in argv:
        tag = argv[argv.index("--tag") + 1].strip()
        return blocked(summary + f"; tag {tag!r} is not a sprint snapshot (sprint-N)") if real == 0 and not RELEASE_TAG_OK.match(tag) else 0
    if "--check" in argv:
        print(summary)
        return blocked(summary) if real == 0 else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
