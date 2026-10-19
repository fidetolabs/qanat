#!/bin/sh
set -e

PROJECT_DIR="${QANAT_PROJECT_DIR:-/project}"
cd "$PROJECT_DIR"

if [ "${QANAT_RESET:-0}" = "1" ]; then
  echo "QANAT_RESET=1 — removing demo project files in $PROJECT_DIR ..."
  rm -f qanat.yaml README.md
  rm -rf data steps universes 2>/dev/null || true
fi

if [ ! -f qanat.yaml ]; then
  echo "Scaffolding demo project in $PROJECT_DIR ..."
  # --store here rather than rewriting qanat.yaml afterwards: --demo runs the
  # pipeline as part of init, and it has to build into the store this container
  # actually uses.
  #
  # --demo reaches SEC EDGAR, which wants a contact in the User-Agent. Without
  # SEC_USER_AGENT in the environment the fetch fails and the container comes up
  # with an empty project rather than a wrong one.
  if [ -n "$QANAT_STORE" ]; then
    qanat init . --name demo --force --demo --store "$QANAT_STORE"
  else
    qanat init . --name demo --force --demo
  fi
fi

HOST="${QANAT_HOST:-127.0.0.1}"
PORT="${QANAT_PORT:-8421}"

exec qanat serve --host "$HOST" --port "$PORT" --run-now
