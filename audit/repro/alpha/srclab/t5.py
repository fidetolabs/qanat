import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
import pandas as pd

CSV = ("date,symbol,close\n"
       "2024-01-01,A,100\n"
       "2024-01-01T00:00:00Z,B,200\n"
       "2024-01-01 09:00:00+09:00,C,300\n"   # == 2024-01-01T00:00:00Z
       "2024-01-02T00:00:00Z,D,400\n")
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
       "options": {"path": "./seed/bars.csv"}}
root = make("t5_tz", src)
(root / "seed/bars.csv").write_text(CSV)
p, r = load(root); store = Store(p.store_url(r))
print("poll:", run_source(store, p, r, p.sources[0]))
show(store)
print("pandas dtype of date:", pd.read_csv(root/'seed/bars.csv').dtypes['date'])
print("time_column:", store.time_column("raw.bars"))
print("max_time   :", store.max_time("raw.bars"))
print()
for a in ["2024-01-01", "2024-01-01T00:00:00", "2024-01-01 09:00:00", "2024-01-02"]:
    df = store.read("raw.bars", as_of=a)
    print(f"as_of {a!r:30} -> {sorted(df['symbol'].tolist())}")
print()
print("raw CAST of each value:")
print(store.query('SELECT symbol, date, TRY_CAST(date AS TIMESTAMP) AS ts FROM raw__bars').to_string())
store.close()
