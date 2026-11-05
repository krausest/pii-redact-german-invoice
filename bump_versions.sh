#!/usr/bin/env bash
# Bump uv, its managed Python, the Python lockfile and the frontend's npm deps.
#   ./bump_versions.sh          # stay within the declared version ranges
#   ./bump_versions.sh --major  # also lift npm ranges to the latest majors
set -euo pipefail
cd "$(dirname "$0")"

major=false
[[ "${1:-}" == "--major" ]] && major=true

echo "== uv"
uv self update

# Latest patch of each installed minor; the venv follows it. A new minor means
# raising requires-python in pyproject.toml and `FROM python:` in the Dockerfile.
echo "== Python"
uv python upgrade
uv run --no-sync python --version

echo "== Python packages"
uv tree --outdated --depth 1 | grep latest || true
uv lock --upgrade
uv sync

echo "== npm"
(
  cd frontend
  npm outdated || true
  if $major; then
    npx --yes npm-check-updates -u
    npm install
  else
    npm update
  fi
)

echo "== checks"
uv run pytest -m 'not slow' -q
(cd frontend && npm run check && npm run build)

echo "Done. Review: git diff uv.lock frontend/package*.json — new/removed deps need docs/LICENSE.md."
echo
echo "== pyproject.toml floors"
uv run --no-sync python check_floors.py
