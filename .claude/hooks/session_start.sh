#!/usr/bin/env bash
# SessionStart: plain stdout is added to Claude's context.
root="${CLAUDE_PROJECT_DIR:-.}"

echo "MEP Co-pilot session context"
echo "Non-negotiables: see CLAUDE.md. AI proposes, code decides, validator measures, human signs."

if [ -f "$root/docs/STATUS.md" ]; then
  echo "--- docs/STATUS.md (first 40 lines) ---"
  head -n 40 "$root/docs/STATUS.md"
fi

if git -C "$root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "--- git ---"
  echo "Branch: $(git -C "$root" branch --show-current)"
  git -C "$root" status --short | head -n 20
fi

draft_count=$(grep -rl "status: draft" "$root/rules" 2>/dev/null | wc -l | tr -d ' ')
echo "Draft rules awaiting engineer review: ${draft_count:-0}"
exit 0
