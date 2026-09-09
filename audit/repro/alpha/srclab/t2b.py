import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

# poll1: date,symbol,close  -> poll2: symbol,date,close  (both VARCHAR: types agree)
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
       "key": ["date", "symbol"], "options": {"path": "./seed/bars.csv"}}
root = make("t2b_swap", src)
(root / "seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n")
p, r = load(root); store = Store(p.store_url(r))
print("poll1:", run_source(store, p, r, p.sources[0]))
(root / "seed/bars.csv").write_text("symbol,date,close\nA,2024-01-03,102\nB,2024-01-03,202\n")
res = run_source(store, p, r, p.sources[0])
print("poll2:", res)
show(store)
print(); print("events:"); events(store, 4)
store.close()
