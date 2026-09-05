#!/bin/sh
# Live ingest demo (~2 minutes): mock feed + qanat console + scheduled job triggers.
#
#   sh scripts/demo-run.sh
#
# Open http://127.0.0.1:8420 and watch bars ingest → normalize → features → weights.

set -eu

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FEED_HOST="${DEMO_FEED_HOST:-127.0.0.1}"
FEED_PORT="${DEMO_FEED_PORT:-8765}"
QANAT_PORT="${QANAT_HOST_PORT:-8420}"
DURATION="${DEMO_DURATION:-120}"
INTERVAL="${DEMO_INTERVAL:-12}"

FEED_PID=""
QANAT_PID=""
TRIGGER_PID=""

cleanup() {
  kill "$TRIGGER_PID" "$QANAT_PID" "$FEED_PID" 2>/dev/null || true
  wait "$TRIGGER_PID" "$QANAT_PID" "$FEED_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

if ! command -v uv >/dev/null 2>&1; then
  echo "demo-run: uv is required" >&2
  exit 1
fi

echo "demo-run: starting mock feed on http://${FEED_HOST}:${FEED_PORT}"
python3 "$ROOT/scripts/demo-feed.py" &
FEED_PID=$!

for _ in $(seq 1 30); do
  if curl -sf "http://${FEED_HOST}:${FEED_PORT}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done
curl -sf "http://${FEED_HOST}:${FEED_PORT}/health" >/dev/null || {
  echo "demo-run: feed did not start" >&2
  exit 1
}

echo "demo-run: bootstrapping history + first pipeline pass"
cd "$ROOT/examples/scheduled-ingest"
uv run --project "$ROOT" qanat check
uv run --project "$ROOT" qanat run bars_seed
for job in news normalize momentum risk tone portfolio; do
  uv run --project "$ROOT" qanat run "$job"
done

echo "demo-run: console → http://127.0.0.1:${QANAT_PORT}"
uv run --project "$ROOT" qanat serve --host 127.0.0.1 --port "$QANAT_PORT" --run-now &
QANAT_PID=$!

for _ in $(seq 1 40); do
  if curl -sf "http://127.0.0.1:${QANAT_PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

run_job() {
  curl -sf -X POST "http://127.0.0.1:${QANAT_PORT}/api/jobs/$1/run" >/dev/null || true
}

trigger_loop() {
  end=$(($(date +%s) + DURATION))
  cycle=0
  while [ "$(date +%s)" -lt "$end" ]; do
    cycle=$((cycle + 1))
    echo "demo-run: live cycle ${cycle} — ingest bars"
    run_job bars
    sleep 2
    run_job normalize
    sleep 1
    run_job momentum
    run_job risk
    sleep 1
    if [ $((cycle % 2)) -eq 0 ]; then
      run_job news
      run_job tone
    fi
    run_job portfolio
    sleep "$INTERVAL"
  done
  echo "demo-run: live cycles finished (${DURATION}s) — console still running"
}

trigger_loop &
TRIGGER_PID=$!

echo ""
echo "  Open http://127.0.0.1:${QANAT_PORT}"
echo "  Live ingest runs for ~${DURATION}s (every ${INTERVAL}s)."
echo "  Press Ctrl+C to stop feed + console."
echo ""

wait "$QANAT_PID"
