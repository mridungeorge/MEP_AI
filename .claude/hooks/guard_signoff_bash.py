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
ENCODED = re.compile(r"(?<![\w-])-(enc|encodedcommand|ec)\b", re.IGNORECASE)
GOLDEN_PROOF = re.compile(r"golden.*(data_agreement|meta\.yaml)|(data_agreement|meta\.yaml).*golden", re.IGNORECASE | re.DOTALL)
OVERRIDE = re.compile(r"MEP_HUMAN_SIGNOFF", re.IGNORECASE)


def _plain(command: str) -> str:
    """The command with quotes and empty-string splices removed (so `appr''oved` and `core.hooks""Path` read as the words
    they build) and Windows path separators turned into `/` (so a path under rules with backslashes reads as a rules path)."""
    return re.sub(r"[\"'`]", "", command).replace("\\", "/")


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        sys.exit(0)
    command = (data.get("tool_input") or {}).get("command", "")
    command = _plain(command)
    if SKIP_HOOKS.search(command):
        print("Blocked by project guardrail: do not skip or redirect git hooks (--no-verify, core.hooksPath).",
              file=sys.stderr)
        sys.exit(2)
    if GOLDEN_PROOF.search(command) and WRITERS.search(command):
        print("Blocked by project guardrail: what makes a golden project REAL (meta.yaml, data_agreement files) is "
              "supplied by a human with the pilot firm, not written from the shell.", file=sys.stderr)
        sys.exit(2)
    if ENCODED.search(command):
        print("Blocked by project guardrail: -EncodedCommand hides the command from the sign-off and database guards; "
              "pass the command as plain text.", file=sys.stderr)
        sys.exit(2)
    if OVERRIDE.search(command):
        print("Blocked by project guardrail: MEP_HUMAN_SIGNOFF is for the human engineer's own shell; an agent must "
              "not set it.", file=sys.stderr)
        sys.exit(2)
    if (TOUCHES_RULES.search(command) or RULE_FILE.search(command)) and FIELD.search(command) and WRITERS.search(command):
        print("Blocked by project guardrail: do not write sign-off or approval fields into rule YAML from the shell. "
              "Only the human engineer fills reviewed_by, reviewed_on, reviewer_registration_no, checked_by, checked_on "
              "or sets status: approved.", file=sys.stderr)
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
