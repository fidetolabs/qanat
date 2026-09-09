import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

for label, url in [
    ("good csv",  "http://127.0.0.1:8731/bars.csv"),
    ("404",       "http://127.0.0.1:8731/nope.csv"),
    ("HTML page", "http://127.0.0.1:8731/page.html"),
    ("dead port", "http://127.0.0.1:8799/bars.csv"),
    ("HTTPS typo","htp://127.0.0.1:8731/bars.csv"),
]:
    name = "t7_" + label.replace(" ", "_")
    src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "replace",
           "options": {"path": url}}
    root = make(name, src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label} ({url})")
    print(f"    status={res.status} rows={res.rows}")
    print(f"    err={res.error}")
    if res.status == "ok": show(store)
    store.close(); print()
