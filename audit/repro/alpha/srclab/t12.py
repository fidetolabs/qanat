import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

def go(label, text, opts=None, binary=None):
    o = {"path": "./seed/bars.csv"}
    o.update(opts or {})
    src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":"replace","options":o}
    root = make("t12_"+"".join(c if (c.isalnum() or c=="_") else "_" for c in label), src)
    if binary is not None: (root/"seed/bars.csv").write_bytes(binary)
    else: (root/"seed/bars.csv").write_text(text)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: {res.status} rows={res.rows} err={res.error}")
    if res.status=="ok": show(store)
    store.close(); print()

go("semicolon separated", "date;symbol;close\n2024-01-01;A;100,5\n")
go("semicolon + sep option (ignored?)", "date;symbol;close\n2024-01-01;A;100\n", {"sep": ";"})
go("dtype option (ignored?)", "date,symbol,close\n2024-01-01,00123,100\n", {"dtype": {"symbol": "str"}})
go("latin-1 encoding", "", binary="date,symbol,close\n2024-01-01,CAF\xc9,100\n".encode("latin-1"))
go("missing local file", "x", {"path": "./seed/nope.csv"})
go("path is a directory", "x", {"path": "./seed"})
go("absolute path", "date,symbol,close\n2024-01-01,A,100\n", {"path": "/etc/hosts"})