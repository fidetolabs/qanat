import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

# NYC close 2024-01-01 16:00 EST == 2024-01-01T21:00Z. A UTC row at 21:00Z is the
# same instant. An as-of of 2024-01-01T17:00:00 must NOT see either.
CSV = ("ts,symbol,close\n"
       "2024-01-01T21:00:00Z,UTC_ROW,100\n"
       "2024-01-01 16:00:00-05:00,EST_ROW,200\n")
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
       "options": {"path": "./seed/bars.csv"}}
root = make("t5b_leak", src)
(root / "seed/bars.csv").write_text(CSV)
p, r = load(root); store = Store(p.store_url(r))
run_source(store, p, r, p.sources[0])
print(store.query('SELECT symbol, ts, CAST(ts AS TIMESTAMP) AS cast_ts FROM raw__bars').to_string())
print("max_time:", store.max_time("raw.bars"))
for a in ["2024-01-01T17:00:00", "2024-01-01T20:00:00", "2024-01-01T21:00:00"]:
    print(f"as_of {a} -> {sorted(store.read('raw.bars', as_of=a)['symbol'].tolist())}")
store.close()
