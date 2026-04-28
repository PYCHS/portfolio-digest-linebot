#!/usr/bin/env bash
# Wrapper for cron (and ad-hoc shell use). Resolves the project root from this
# script's location, sources .env (cron does not load shell profiles), prefers
# the project venv if present, and forwards args to `python -m src.main`.
#
# Usage:
#   scripts/run_digest.sh                # default (dry-run)
#   scripts/run_digest.sh --push         # push to LINE
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -x .venv/bin/python ]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

exec "$PYTHON" -m src.main "$@"
