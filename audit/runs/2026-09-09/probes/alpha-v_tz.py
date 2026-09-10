"""Verify independently: does a timezone offset let a row through the as-of cut early?"""
import tempfile, pathlib
import pandas as pd
from qanat.store import Store

tmp = pathlib.Path(tempfile.mkdtemp())
s = Store(tmp / "t.duckdb")

# three rows that are the SAME INSTANT written three ways
df = pd.DataFrame([
    {"ts": "2024-01-01T21:00:00Z",       "symbol": "UTC_ROW",   "close": 1.0},
    {"ts": "2024-01-01 16:00:00-05:00",  "symbol": "NY_ROW",    "close": 2.0},
    {"ts": "2024-01-02 06:00:00+09:00",  "symbol": "SEOUL_ROW", "close": 3.0},
])
s.write("raw.bars", df)
print("all three rows are the same instant: 2024-01-01 21:00 UTC\n")
print(s.query('SELECT symbol, ts, CAST(ts AS TIMESTAMP) AS cast_ts FROM raw__bars').to_string(index=False))
print()
print("max_time reported:", s.max_time("raw.bars"))
print()
for cut in ["2024-01-01T17:00:00", "2024-01-01T20:00:00", "2024-01-01T22:00:00", "2024-01-02T07:00:00"]:
    got = sorted(s.read("raw.bars", as_of=cut)["symbol"])
    print(f"  as_of {cut} (UTC) -> visible: {got}")
print()
print("  correct answer: nothing is visible before 21:00 UTC; all three at/after it.")

# and through the real replay machinery
print()
print("through open_pit(), the views a replay actually reads:")
s.open_pit("2024-01-01T17:00:00")
print("  as-of view at 17:00 UTC ->", sorted(s.query('SELECT symbol FROM qanat_pit.raw__bars')["symbol"]))
s.close_pit()

# fetched_at from the rest connector
print()
from datetime import datetime, timezone
env = pd.DataFrame([{"fetched_at": datetime.now(timezone.utc), "source_id": "px",
                     "symbol": None, "payload": "{}"}])
s.write("raw.api", env)
info = s.table_info("raw.api")
print("rest _envelope fetched_at column type:", dict(info.columns)["fetched_at"])
print("  store.max_time :", s.max_time("raw.api"))
print("  true utcnow    :", datetime.now(timezone.utc).replace(tzinfo=None))
print("  duckdb TimeZone:", s.query("SELECT current_setting('TimeZone') v")["v"][0])
n = len(s.read("raw.api", as_of=datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
print(f"  rows visible at as_of = 'now in UTC': {n} of 1")
