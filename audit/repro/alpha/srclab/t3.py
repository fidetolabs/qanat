import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
import pandas as pd

BASE = "date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n"
CASES = {
  "N/A in numeric":  "date,symbol,close\n2024-01-03,A,N/A\n2024-01-04,A,103\n",
  "empty in numeric":"date,symbol,close\n2024-01-03,A,\n2024-01-04,A,103\n",
  "text in numeric": "date,symbol,close\n2024-01-03,A,oops\n2024-01-04,A,103\n",
  "float in int col":"date,symbol,close\n2024-01-03,A,102.5\n",
}
for label, second in CASES.items():
    name = "t3_" + label.replace(" ", "_").replace("/", "")
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
           "options": {"path": "./seed/bars.csv"}}
    root = make(name, src)
    (root / "seed/bars.csv").write_text(BASE)
    p, r = load(root); store = Store(p.store_url(r))
    run_source(store, p, r, p.sources[0])
    (root / "seed/bars.csv").write_text(second)
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: poll2 status={res.status} rows={res.rows} err={res.error}")
    # what pandas parsed
    print("   pandas dtypes:", dict(pd.read_csv(root/'seed/bars.csv').dtypes.astype(str)))
    show(store)
    store.close(); print()

# first-poll-only variant: NA present from the very start (replace mode)
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
       "options": {"path": "./seed/bars.csv"}}
root = make("t3_first", src)
(root / "seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,N/A\n2024-01-02,A,101\n")
p, r = load(root); store = Store(p.store_url(r))
print("=== NA on the FIRST poll, replace mode:", run_source(store, p, r, p.sources[0]))
show(store); store.close()
