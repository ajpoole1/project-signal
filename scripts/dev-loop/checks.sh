#!/usr/bin/env bash
# Canonical check script — the single definition of green for project-signal.
# Invoked identically by agents (locally) and CI (.github/workflows/quality.yml).
# Do not narrow, skip, or reinterpret these steps to reach green.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "--- ruff check ---"
ruff check .

echo "--- ruff format ---"
ruff format --check .

echo "--- pytest ---"
python -m pytest

echo "--- all green ---"
