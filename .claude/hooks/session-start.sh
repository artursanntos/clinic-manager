#!/usr/bin/env bash
# Installs dependencies in Claude Code cloud sessions so tests, ruff and mypy work right away.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"
uv sync --all-extras --quiet
echo "Dependencies installed (uv sync --all-extras)."
