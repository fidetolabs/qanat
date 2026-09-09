import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

BASE = "date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n"
for label, second, mode in [
    ("headers only, append", "date,symbol,close\n", "append"),
    ("headers only, replace", "date,symbol,close\n", "replace"),
    ("0 bytes, append", "", "append"),
]:
    name = "t6_" + label.replace(" ", "_").replace(",", "")
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": mode,
           "options": {"path": "./seed/bars.csv"}}
    root = make(name, src)
    (root / "seed/bars.csv").write_text(BASE)
    p, r = load(root); store = Store(p.store_url(r))
    run_source(store, p, r, p.sources[0])
    (root / "seed/bars.csv").write_text(second)
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: poll2 status={res.status} rows={res.rows} err={res.error}")
    show(store)
    print("  events:"); events(store, 3)
    store.close(); print()

# 0 bytes on the FIRST poll
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
       "options": {"path": "./seed/bars.csv"}}
root = make("t6_empty_first", src)
(root / "seed/bars.csv").write_text("")
p, r = load(root); store = Store(p.store_url(r))
res = run_source(store, p, r, p.sources[0])
print("=== 0 bytes on FIRST poll:", res.status, res.rows, res.error)
events(store, 2)
store.close()
