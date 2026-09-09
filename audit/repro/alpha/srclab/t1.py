import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *

CSV = "date,symbol,close\n2024-01-01,A,100\n2024-01-01,B,200\n2024-01-02,A,101\n2024-01-02,B,201\n"

def run(name, key=None):
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
           "options": {"path": "./seed/bars.csv"}}
    if key: src["key"] = key
    root = make(name, src)
    (root / "seed/bars.csv").write_text(CSV)
    from qanat.project import load
    from qanat.runner import run_source
    from qanat.store import Store
    p, r = load(root)
    store = Store(p.store_url(r))
    for i in (1, 2):
        res = run_source(store, p, r, p.sources[0])
        print(f"--- {name} poll {i}: status={res.status} rows={res.rows} err={res.error}")
        show(store)
    print("  events:"); events(store, 6)
    store.close()

print("=== 1a: append, NO key ===")
run("t1a_nokey")
print()
print("=== 1b: append, key=[date,symbol] ===")
run("t1b_key", key=["date", "symbol"])
print()
print("=== 1c: append, key=[date,ticker]  (ticker does not exist) ===")
run("t1c_badkey", key=["date", "ticker"])
