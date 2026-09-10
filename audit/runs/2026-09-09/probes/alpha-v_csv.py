"""Verify: zero-padded tickers, and a column reorder in the feed."""
import tempfile, pathlib
import pandas as pd
from qanat.models import Source
from qanat.sources import fetch
from qanat.store import Store

tmp = pathlib.Path(tempfile.mkdtemp()); (tmp/"seed").mkdir()
src = Source(id="bars", to=["raw.bars"], connector="csv", mode="append",
             options={"path": "./seed/bars.csv"})

print("=" * 78)
print("Zero-padded tickers (KRX/HKEX codes) through the csv connector")
print("=" * 78)
(tmp/"seed/bars.csv").write_text(
    "date,symbol,close\n"
    "2024-01-01,005930,71000\n"      # Samsung Electronics
    "2024-01-01,000660,128000\n"     # SK hynix
    "2024-01-01,00700,350.5\n")      # Tencent, HKEX
df = fetch(src, tmp)
print(df.to_string(index=False))
print()
print("  symbol dtype:", df["symbol"].dtype)
print("  -> 005930 became", repr(df['symbol'].iloc[0]), "  Samsung's code is destroyed")
print("  -> the universe file lists '005930' as text, so the join finds nothing")

print()
print("=" * 78)
print("A feed that reorders its columns between polls (same names, same types)")
print("=" * 78)
s = Store(tmp/"t.duckdb")
(tmp/"seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n")
s.write("raw.bars", fetch(src, tmp), mode="append")
print("  poll 1 ok")
(tmp/"seed/bars.csv").write_text("symbol,date,close\nA,2024-01-03,102\nB,2024-01-03,202\n")
d2 = fetch(src, tmp)
print("  poll 2 arrives as:", list(d2.columns))
n = s.write("raw.bars", d2, mode="append")
print(f"  write returned rows={n}, no error")
print()
print(s.query('SELECT * FROM raw__bars').to_string(index=False))
print()
print("  -> the date column now holds 'A' and 'B'; the symbol column holds dates.")
print("     INSERT INTO ... SELECT * matches by POSITION, not by name.")
