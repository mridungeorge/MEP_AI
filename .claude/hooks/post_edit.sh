#!/usr/bin/env bash
# PostToolUse: lint Python, validate rule YAML against the schema. Both run INSIDE the devcontainer (never on the host).
# Exit 2 shows stderr to Claude (the edit already happened; Claude should fix it).
# If the container cannot be reached the lint is skipped (exit 0): the Stop hook is the hard gate and blocks in that case.
set -uo pipefail

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-.}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
errors=""

in_container() {  # $1 = shell command, run in the project folder of the devcontainer
  python3 "$here/devcontainer_exec.py" --timeout 120 -- "$1"
}
# the file's path inside the container (the working tree is bind-mounted); empty when the container cannot be reached
cpath="$(python3 -c '
import os, sys
sys.path.insert(0, sys.argv[1])
import devcontainer_exec as d
root, f = sys.argv[2], sys.argv[3]
found = d.find_container(root)
print(d.to_container_path(root, f, found[1]) if found else "")
' "$here" "$root" "$file" 2>/dev/null)"
[ -z "$cpath" ] && exit 0

case "$file" in
  *.py)
    # EXE001/EXE002 depend on file modes, which the Windows bind mount fakes; CI checks them on a real checkout
    out="$(in_container "uv run --no-sync ruff check --quiet --ignore EXE001,EXE002 '$cpath'" 2>&1)"
    rc=$?
    [ "$rc" -eq 125 ] && exit 0
    [ "$rc" -ne 0 ] && errors+="ruff:\n$out\n"
    ;;
  */rules/*.yaml|*/rules/*.yml)
    out="$(in_container "uv run --no-sync python scripts/validate_rules.py '$cpath'" 2>&1)"
    rc=$?
    [ "$rc" -eq 125 ] && exit 0
    [ "$rc" -ne 0 ] && errors+="rule schema:\n$out\n"
    ;;
esac

if [ -n "$errors" ]; then
  printf "Post-edit checks failed for %s:\n%b" "$file" "$errors" >&2
  exit 2
fi
exit 0
