#!/usr/bin/env python3
"""PreToolUse (Bash and PowerShell): keep agents from writing sign-off or approval into rule YAML through the shell.

The Edit/Write hook compares rule files before and after the change; a shell command (sed -i, tee, a heredoc, a Python
one-liner, git apply ...) can change a rule file without that comparison. This hook blocks Bash commands that name a
rule YAML file together with a sign-off field or `approved`, and any attempt to set the commit tripwire override.
It is a tripwire, not a lock: the controls that matter are the human review of the commit and CI. See
docs/engine-conventions.md section 7.
"""
import json
import re
import sys

RULE_FILE = re.compile(r"rules[/\\][^\s\"']*\.ya?ml|rules[/\\]\*|\.ya?ml", re.IGNORECASE)
FIELD = re.compile(r"(reviewed_by|reviewed_on|reviewer_registration_no|checked_by|checked_on|\bapproved\b)", re.IGNORECASE)
TOUCHES_RULES = re.compile(r"rules[/\\]", re.IGNORECASE)
SKIP_HOOKS = re.compile(r"--no-ve(r(i(f(y)?)?)?)?\b|hookspath|\bgit\b[^|;&\n]*\bcommit\b[^|;&\n]*\s-[a-zA-Z]*n[a-zA-Z]*\b",
                        re.IGNORECASE)
# Commands that write or move a file. PowerShell cmdlets and aliases are listed with the POSIX tools: the PowerShell tool
# sends the same `command` field, and `(Get-Content f) -replace a,b | Set-Content f` is the sed -i of that shell.
WRITERS = re.compile(
    r"\b(sed|awk|perl|tee|python3?|ruby|node|cat|echo|printf|cp|mv|git\s+(apply|checkout|restore|stash)"
    r"|set-content|add-content|out-file|copy-item|move-item|rename-item|new-item|clear-content|invoke-expression"
    r"|sc|ac|ni|cpi|mi|ren|iex|copy|move|rni|xcopy|robocopy|streamwriter|filestream)\b|(?<![.\w])py\b"
    r"|>>?|<<|-replace\b|\b(write|append)(all)?(text|lines|bytes)\b|\[(system\.)?io\.file\]", re.IGNORECASE)
# an encoded command hides what it runs from every check here; an agent has no need for one
ENCODED = re.compile(r"\b(pwsh|powershell)(\.exe)?\b[^|;&\n]*\s-(e|ec|enc|encodedcommand)\b", re.IGNORECASE)
GOLDEN_PROOF = re.compile(r"golden.*(data_agreement|meta\.yaml)|(data_agreement|meta\.yaml).*golden", re.IGNORECASE | re.DOTALL)
OVERRIDE = re.compile(r"MEP_HUMAN_SIGNOFF", re.IGNORECASE)


def _variants(command: str) -> list[str]:
    """The command read two ways, because the two shells disagree about a backslash: bash deletes it (a backslash
    inside the word approved still spells approved), Windows uses it as a path separator (a path under rules). Quotes and empty-string splices
    are removed in both (`appr''oved`, `core.hooks""Path`). A rule must hold under NEITHER reading to let a command through."""
    unquoted = re.sub(r"[\"'`]", "", command)
    return [unquoted.replace("\\", ""), unquoted.replace("\\", "/")]


def verdict(command: str) -> str | None:
    """The reason a command is blocked, or None."""
    for text in _variants(command):
        if SKIP_HOOKS.search(text):
            return "do not skip or redirect git hooks (--no-verify, core.hooksPath)."
        if GOLDEN_PROOF.search(text) and WRITERS.search(text):
            return ("what makes a golden project REAL (meta.yaml, data_agreement files) is supplied by a human with the pilot "
                    "firm, not written from the shell.")
        if ENCODED.search(text):
            return "-EncodedCommand hides the command from the sign-off and database guards; pass the command as plain text."
        if OVERRIDE.search(text):
            return "MEP_HUMAN_SIGNOFF is for the human engineer's own shell; an agent must not set it."
        if (TOUCHES_RULES.search(text) or RULE_FILE.search(text)) and FIELD.search(text) and WRITERS.search(text):
            return ("do not write sign-off or approval fields into rule YAML from the shell. Only the human engineer fills "
                    "reviewed_by, reviewed_on, reviewer_registration_no, checked_by, checked_on or sets status: approved.")
    return None


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command", "")
    reason = verdict(command if isinstance(command, str) else "")
    if reason:
        print(f"Blocked by project guardrail: {reason}", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
