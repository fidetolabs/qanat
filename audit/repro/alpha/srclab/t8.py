import sys, os; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from jsonserver import serve
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
serve(8732)
U = "http://127.0.0.1:8732"

CASES = [
 ("plain list",       {"url": f"{U}/list"}),
 ("records dot-path", {"url": f"{U}/nested", "records": "data.items"}),
 ("bad dot-path",     {"url": f"{U}/nested", "records": "data.nope"}),
 ("orient index",     {"url": f"{U}/indexed", "orient": "index", "index_column": "date"}),
 ("orient index, no index_column", {"url": f"{U}/indexed", "orient": "index"}),
 ("payload envelope", {"url": f"{U}/nested", "payload": True}),
 ("per-symbol both ok", {"url": U + "/sym/{symbol}", "symbols": ["A", "B"]}),
 ("per-symbol 2nd 404", {"url": U + "/half/{symbol}", "symbols": ["A", "B"]}),
 ("500",              {"url": f"{U}/boom"}),
 ("dict not list",    {"url": f"{U}/dict"}),
 ("scalar body",      {"url": f"{U}/scalar"}),
 ("null body",        {"url": f"{U}/nulllist"}),
 ("not json",         {"url": f"{U}/notjson"}),
 ("no url",           {"records": "x"}),
]
for label, opts in CASES:
    name = "t8_" + label.replace(" ", "_").replace(",", "")
    src = {"id": "px", "to": ["raw.px"], "connector": "rest", "mode": "replace", "options": opts}
    root = make(name, src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}")
    print(f"    status={res.status} rows={res.rows}")
    if res.error: print(f"    err={res.error}")
    if res.status == "ok": show(store, "raw.px")
    else:
        for e in store.recent_events(1): print("    event:", e["message"])
    store.close(); print()
