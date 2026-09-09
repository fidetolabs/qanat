import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

BASE_CSV = "date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n"

VARIANTS = {
 "extra col": "date,symbol,close,volume\n2024-01-03,A,102,5000\n",
 "missing col": "date,symbol\n2024-01-03,A\n",
 "reordered": "close,symbol,date\n102,A,2024-01-03\n",
 "renamed col": "date,ticker,close\n2024-01-03,A,102\n",
}

for label, second in VARIANTS.items():
    name = "t2_" + label.replace(" ", "_")
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
           "options": {"path": "./seed/bars.csv"}}
    root = make(name, src)
    (root / "seed/bars.csv").write_text(BASE_CSV)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: poll1 status={res.status} rows={res.rows}")
    (root / "seed/bars.csv").write_text(second)
    res = run_source(store, p, r, p.sources[0])
    print(f"    poll2 status={res.status} rows={res.rows} err={res.error}")
    show(store)
    store.close()
    print()
