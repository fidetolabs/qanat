import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
for label, url in [("html w/ commas","http://127.0.0.1:8731/err.html"),
                   ("soft-404 body (HTTP 200)","http://127.0.0.1:8731/soft404.html")]:
    name = "t7b_" + label.split()[0]
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
           "options": {"path": url}}
    root = make(name, src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: status={res.status} rows={res.rows} err={res.error}")
    if res.status=="ok": show(store)
    print("  events:"); events(store,2)
    store.close(); print()
