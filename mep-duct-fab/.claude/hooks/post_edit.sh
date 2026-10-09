#!/usr/bin/env bash
# PostToolUse: lint Python, validate rule YAML against the schema.
# Exit 2 shows stderr to Claude (the edit already happened; Claude should fix it).
set -uo pipefail

input="$(cat)"
file="$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print((d.get("tool_input") or {}).get("file_path",""))' 2>/dev/null)"
[ -z "$file" ] && exit 0
[ -f "$file" ] || exit 0

root="${CLAUDE_PROJECT_DIR:-.}"
errors=""

case "$file" in
  *.py)
    if command -v ruff >/dev/null 2>&1; then
      out="$(ruff check --quiet "$file" 2>&1)" || errors+="ruff:\n$out\n"
    fi
    ;;
  */rules/*.yaml|*/rules/*.yml)
    if [ -f "$root/scripts/validate_rules.py" ]; then
      out="$(python3 "$root/scripts/validate_rules.py" "$file" 2>&1)" || errors+="rule schema:\n$out\n"
    fi
    ;;
esac

if [ -n "$errors" ]; then
  printf "Post-edit checks failed for %s:\n%b" "$file" "$errors" >&2
  exit 2
fi
exit 0
