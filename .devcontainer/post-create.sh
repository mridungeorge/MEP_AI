#!/usr/bin/env bash
# Runs once after the container is built: same toolchain as .github/workflows/ci.yml.
set -euo pipefail
pip install --quiet uv
uv sync --group dev --locked
pnpm install --frozen-lockfile
python --version | grep -q "3.12" || { echo "devcontainer: expected Python 3.12" >&2; exit 1; }
git config core.hooksPath .githooks 2>/dev/null || true   # release-claim hooks
echo "devcontainer ready: run bash scripts/ci.sh (needs Docker for Supabase; docker-in-docker is enabled)"
