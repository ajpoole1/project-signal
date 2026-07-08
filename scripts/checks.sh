#!/usr/bin/env bash
# Canonical check script — the single definition of green for project-signal.
# CI invokes this file directly (.github/workflows/quality.yml); agents run it locally.
# Both execute the same bytes, so local and CI cannot disagree on what green means.
# Do not narrow, skip, or reinterpret these steps to reach green.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "--- ruff check ---"
ruff check .

echo "--- ruff format ---"
ruff format --check .

echo "--- pytest ---"
pytest --cov --cov-report=term-missing

echo "--- all green ---"
