import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from jsonserver import serve
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
serve(8736); U = "http://127.0.0.1:8736"
def run(label, opts, mode="replace"):
    src = {"id":"px","to":["raw.px"],"connector":"rest","mode":mode,"options":opts}
    root = make("t11_"+label.replace(" ","_"), src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: {res.status} rows={res.rows} err={res.error}")
    if res.status=="ok": show(store,"raw.px")
    store.close(); print()

run("columns: keeps a column that is not there", {"url": f"{U}/list", "columns": ["date","symbol","close","volume"]})
run("columns: ALL missing", {"url": f"{U}/list", "columns": ["a","b"]})
run("rename: source key not present", {"url": f"{U}/list", "rename": {"t":"ts","close":"px"}})
run("rename: two cols onto one name", {"url": f"{U}/list", "rename": {"symbol":"date"}})

# csv float precision through the whole path
print("=== csv: high-precision + big ints")
src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":"replace","options":{"path":"./seed/bars.csv"}}
root = make("t11_prec", src)
(root/"seed/bars.csv").write_text(
  "date,symbol,close,shares,id\n"
  "2024-01-01,A,0.000000123456789,12345678901234567890,00123\n"
  "2024-01-02,A,1e400,1,00456\n")
p, r = load(root); store = Store(p.store_url(r))
print(" ", run_source(store, p, r, p.sources[0]))
show(store)
store.close()
