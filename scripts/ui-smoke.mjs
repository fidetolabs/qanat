#!/bin/sh
# Console smoke test (Chrome). Requires qanat serve running.
#
#   sh scripts/ui-smoke.mjs
#   BASE=http://127.0.0.1:8420 sh scripts/ui-smoke.mjs
#
# Installs Playwright locally on first run (node_modules/ is gitignored).

set -eu

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v node >/dev/null 2>&1; then
  echo "ui-smoke: node is required" >&2
  exit 1
fi

if ! node -e "require.resolve('playwright')" >/dev/null 2>&1; then
  echo "ui-smoke: installing playwright (one-time)…"
  npm install --no-save playwright@1.49.1
fi

export BASE="${BASE:-http://127.0.0.1:8423}"
echo "ui-smoke: ${BASE}"
exec node "$ROOT/scripts/ui-smoke-run.mjs"
