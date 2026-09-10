"""Epoch-integer timestamps -- the most common clock on the public web."""
import pathlib
from qanat.store import Store
ROOT = pathlib.Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/weblab/proj")
s = Store(ROOT / "data/q.duckdb")
s.query("CREATE OR REPLACE TABLE raw__quakes_flat AS "
        "SELECT id, properties.time AS time, properties.mag AS mag FROM raw__quakes")
print("raw.quakes_flat has a top-level column literally named 'time', holding epoch ms.")
print("  time_column() finds it:", s.time_column("raw.quakes_flat"))
print()
for label, fn in [
    ("store.max_time()",            lambda: s.max_time("raw.quakes_flat")),
    ("store.read(as_of=...)",       lambda: len(s.read("raw.quakes_flat", as_of="2026-09-09"))),
    ("open_pit() -- the replay",    lambda: s.open_pit("2026-09-09")),
]:
    try:
        print(f"  {label:<28} -> {fn()}")
    except Exception as exc:
        print(f"  {label:<28} -> {type(exc).__name__}: {str(exc).splitlines()[0][:78]}")
try: s.close_pit()
except Exception: pass
print()
print("  Every one of those is on the path a backtest takes.")
print("  Epoch seconds/ms is what USGS, GitHub, Slack, Binance, Coinbase and most APIs send.")
print()
print("=" * 88)
print("And the columns qanat WILL accept as a clock:")
print("=" * 88)
from qanat.store import TIME_COLS
print("  ", ", ".join(TIME_COLS))
print()
print("  raw.gdp has a 'Year' column and 13,979 rows of real annual data.")
print("  'year' is not in that list, so qanat finds no clock:")
print("    time_column(raw.gdp) =", s.time_column("raw.gdp"))
print("    -> during a replay this table is NOT filtered; every row is visible at every as-of date.")
print("    -> retention can never expire it.")
print("    -> nothing warns.")
s.close()
