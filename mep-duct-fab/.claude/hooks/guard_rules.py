#!/usr/bin/env python3
"""PreToolUse guard for Edit/Write/MultiEdit.

Blocks edits that break MEP Co-pilot non-negotiables (see CLAUDE.md).
Exit 2 + stderr = blocked; Claude sees the reason and must fix the cause.
Stdlib only.
"""
import json
import os
import re
import sys
from pathlib import Path


def new_text(tool_input: dict) -> str:
    """Collect the text being written, across Write, Edit and MultiEdit."""
    parts = []
    for key in ("content", "new_string"):
        if isinstance(tool_input.get(key), str):
            parts.append(tool_input[key])
    for edit in tool_input.get("edits", []) or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            parts.append(edit["new_string"])
    return "\n".join(parts)


def _guard():
    """The shared sign-off detector (scripts/signoff_guard.py). Fails closed if it cannot be loaded."""
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
    sys.path.insert(0, str(root / "scripts"))
    try:
        import signoff_guard
    except Exception as exc:  # noqa: BLE001 - a broken guard must block, not allow
        print(f"Blocked by project guardrail: cannot load scripts/signoff_guard.py ({exc})", file=sys.stderr)
        sys.exit(2)
    return signoff_guard


def before_and_after(path: str, tool_input: dict) -> tuple[str, str]:
    """The file text before and after the tool call. Reads the file as UTF-8 with its line endings intact; a file that
    is not valid UTF-8 (UTF-16, binary) cannot be judged and raises, which the caller turns into a block."""
    guard = _guard()
    old = ""
    p = Path(path)
    if p.exists():
        with p.open(encoding="utf-8", newline="") as fh:
            old = fh.read()
    if isinstance(tool_input.get("content"), str):  # Write replaces the whole file
        return old, tool_input["content"]
    return old, guard.apply_edits(old, tool_input)


def block(reason: str) -> None:
    print(f"Blocked by project guardrail: {reason}", file=sys.stderr)
    sys.exit(2)


def _is_rule(path: str) -> bool:
    guard = _guard()
    return any(guard.is_rule_path(p) for p in guard.real_paths(path))


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    tool_input = data.get("tool_input", {}) or {}
    path = (tool_input.get("file_path") or "").replace("\\", "/")
    if not path:
        sys.exit(0)
    try:
        run_checks(data, tool_input, path)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a guard that crashes must block, not wave the edit through
        if _looks_guarded(path):
            block(f"the guard could not check this change ({type(exc).__name__}: {exc}); refusing it")
    sys.exit(0)


def _looks_guarded(path: str) -> bool:
    """Whether the path could be a rule file or a golden-project proof file (decided without the failing code)."""
    low = path.lower().replace("\\", "/")
    return "rules" in low or "golden" in low


def run_checks(data: dict, tool_input: dict, path: str) -> None:
    guard = _guard()
    try:
        if Path(path).is_file() and Path(path).stat().st_nlink > 1:
            block("this file has several hard links, so its real path cannot be judged; edit it as a human")
    except OSError:
        pass
    if _is_rule(path) or "golden" in path.lower():
        probe = Path(path)
        if any(p.is_symlink() for p in (probe, *probe.parents)):
            block("a path through a symbolic link cannot be judged; rule and golden files must not be symbolic links")
    text = new_text(tool_input)
    is_py = path.endswith(".py")
    in_engine = "/mep/engine/" in path
    in_agents = "/mep/agents/" in path
    is_test = "/tests/" in path or path.split("/")[-1].startswith("test_")

    # 1. No eval/exec in the engine.
    if in_engine and is_py and re.search(r"\b(eval|exec)\s*\(", text):
        block("eval/exec is forbidden in engine/. Extend the AST-whitelist evaluator instead.")

    # 2. No hard-coded NCC clause IDs in engine Python (they belong in rule YAML).
    if in_engine and is_py and not is_test and re.search(r"['\"]J\d+[DPV]\d+", text):
        block("Clause IDs must live in rules/*.yaml, not in engine code. Read citations from the rule.")

    # 3. Agents must never construct compliance results.
    if in_agents and is_py and not is_test and re.search(
        r"""(result|status)\s*[=:]\s*['"](PASS|FAIL|NEEDS_JUDGEMENT)['"]""", text
    ):
        block("Agent code may not set PASS/FAIL. Results come only from the rule engine via request_rule_run.")

    # 4. Sign-off is human work. Always checked for rule files, EVEN when the new text is empty (a pure deletion such
    #    as removing a '# ' comment marker can turn a commented value into a real one). The file is parsed before and
    #    after the change, so quoted keys, flow style, merge keys, extra documents and partial edits are all seen; an
    #    Edit whose old_string does not match the file is refused.
    if _is_rule(path):
        old_text, after = before_and_after(path, tool_input)
        for why in guard.violations(old_text, after):
            block(f"Only a human engineer approves rules or fills in sign-off and check fields ({why}). Keep "
                  "status: draft and leave reviewed_by, reviewed_on, reviewer_registration_no, checked_by and "
                  "checked_on null.")
        # 5. Never reproduce standards text.
        if re.search(r"""["']?(clause_text|standard_text|verbatim)["']?\s*:""", after, re.MULTILINE):
            block("Rules must not store standards wording. Keep the clause reference and encoded logic only.")

    # 6. What makes a golden project REAL (its meta.yaml saying synthetic: false, a data agreement file) is supplied by
    #    a human with the pilot firm, never by an agent.
    if any(guard.is_golden_real_path(p) for p in guard.real_paths(path)):
        old_text, after = before_and_after(path, tool_input)
        name = path.replace("\\", "/").split("/")[-1].lower()
        if name.startswith("data_agreement"):
            block("data_agreement files come from the pilot firm and are added by a human, not by an agent.")
        if guard.golden_real_change(old_text, after):
            block("a golden project becomes REAL only when a human sets synthetic: false in meta.yaml with the firm's "
                  "data agreement; an agent must not.")


if __name__ == "__main__":
    main()
