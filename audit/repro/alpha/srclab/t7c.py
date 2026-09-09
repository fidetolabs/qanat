import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
import shutil
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
       "options": {"path": "./seed/bars.csv"}}
root = make("t7c_replace_wipe", src)
(root/"seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,100\n2024-01-02,A,101\n")
p, r = load(root); store = Store(p.store_url(r))
run_source(store, p, r, p.sources[0]); print("after good poll:"); show(store)
# feed now answers with an error body instead of prices
(root/"seed/bars.csv").write_text("error,message,code\nauth,session expired,401\n")
res = run_source(store, p, r, p.sources[0])
print("after error-body poll:", res.status, res.rows); show(store)
print("events:"); events(store,3)
store.close()
