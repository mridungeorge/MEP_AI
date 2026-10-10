#!/usr/bin/env bash
# Local mirror of .github/workflows/ci.yml. Run from anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v pnpm >/dev/null 2>&1; then echo "ci.sh: pnpm required"; exit 1; fi
[ -d node_modules ] || pnpm install --frozen-lockfile
if command -v uv >/dev/null 2>&1 && uv sync --group dev --extra agents --locked >/dev/null 2>&1 && uv run python -c "" >/dev/null 2>&1; then
  run() { uv run "$@"; }
else
  echo "ci.sh: WARNING uv cannot launch its venv here; using the system interpreter (not pinned to 3.12)" >&2
  run() { "$@"; }
fi
run python -c "import yaml, jsonschema, pint" || { echo "ci.sh: missing deps (validate_rules.py would skip silently)"; exit 1; }
run python -m ruff check .
run python -m mypy
run python scripts/validate_rules.py
# sign-off/approval changes since the newest *-gate or restore-* tag need an Engineer-Signoff trailer; no tag = fail closed.
# Needs a git checkout with tags (a bare `git archive` has no history: run the gate from a clone).
run python scripts/check_signoff_changes.py --base-tag
run python scripts/release_gate.py --labels   # every golden project is labelled; synthetic ones say so
run python scripts/release_gate.py --scan     # no release claims while zero real golden projects exist
run python scripts/ingest_accuracy.py --check   # the measured-accuracy report is current
pnpm --filter @mep/web run typecheck
pnpm --filter @mep/web run test
run python -m pytest tests/rules tests/engine tests/ingest tests/api tests/diff tests/review tests/db
# the drafting skills: duct-fab (needs cadquery, installed in the devcontainer) and space-envelope; their validators gate every output
run python -m pytest tests/skills
# RLS attack tests need a real Postgres: local Supabase (Docker). Fails loudly if it is down.
if ! pnpm exec supabase status >/dev/null 2>&1; then pnpm exec supabase start; fi
pnpm exec supabase db reset
run python -m pytest tests/rls
# end to end: real Next.js build + uvicorn on the local Supabase + Chromium (IFC -> health -> confirm -> run; refusals)
(cd apps/web && pnpm exec playwright install chromium && pnpm run e2e)
# review agents must run against a disposable DB with confined roles; prove the isolation properties
run python scripts/review_db.py selftest
grep -q "Database isolation (mandatory)" .claude/agents/adversarial-reviewer.md || { echo "ci.sh: reviewer agent lacks the database isolation rule"; exit 1; }
grep -q guard_signoff_bash .claude/settings.json || { echo "ci.sh: sign-off Bash hook is not registered"; exit 1; }
grep -q '"matcher": "Bash|PowerShell"' .claude/settings.json || { echo "ci.sh: shell guards are not registered for PowerShell"; exit 1; }
grep -q guard_review_db .claude/settings.json || { echo "ci.sh: database isolation hook is not registered"; exit 1; }
# exit 5 = no golden projects yet (Sprint 1+); any other failure is real
rc=0; run python -m pytest tests/golden || rc=$?
[ "$rc" -eq 0 ] || [ "$rc" -eq 5 ] || exit "$rc"
echo "ci.sh: all steps passed"
