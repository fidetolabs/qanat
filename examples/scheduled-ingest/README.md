# Scheduled ingest

Two-minute **real HTTP ingest** demo — a source on a `schedule:`, not live trading: a mock market feed on port **8765** and Qanat polling it
while the console shows rows moving through the pipeline.

From the repo root:

```bash
sh scripts/demo-run.sh
```

Open **http://127.0.0.1:8420**. You should see:

1. History bootstrap (`bars_seed` → `raw.daily_prices`)
2. ~2 minutes of live ticks (`bars` REST source, append mode)
3. Downstream steps firing every ~12 seconds

Environment overrides:

| Variable | Default | Meaning |
| --- | --- | --- |
| `DEMO_FEED_PORT` | `8765` | Mock feed port |
| `QANAT_HOST_PORT` | `8420` | Console port |
| `DEMO_DURATION` | `120` | Live trigger loop (seconds) |
| `DEMO_INTERVAL` | `12` | Seconds between ingest cycles |

Manual run:

```bash
# terminal 1
python3 ../../scripts/demo-feed.py

# terminal 2
uv run --project ../.. qanat run bars_seed
uv run --project ../.. qanat run news normalize momentum risk tone portfolio
uv run --project ../.. qanat serve --run-now
```
