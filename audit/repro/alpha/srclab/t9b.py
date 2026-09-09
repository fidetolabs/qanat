import sys, os; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from jsonserver import serve
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
serve(8735)
U = "http://127.0.0.1:8735"
def run(label, opts):
    name = "t9b_" + label.replace(" ", "_")
    src = {"id": "px", "to": ["raw.px"], "connector": "rest", "mode": "replace", "options": opts}
    root = make(name, src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: status={res.status} rows={res.rows}")
    if res.error: print("    err:", res.error.replace("\n"," | "))
    if res.status=="ok": show(store, "raw.px")
    store.close(); print()

os.environ.pop("MISSING_TOK", None)
os.environ["lower_tok"] = "SECRET"
run("bare ${MISSING} header (no prefix)", {"url": f"{U}/needauth", "headers": {"Authorization": "${MISSING_TOK}"}})
run("lowercase env name in header", {"url": f"{U}/echoauth", "headers": {"X-Key": "${lower_tok}"}})
run("lowercase env name in url", {"url": U + "/${lower_path}list"})
# csv connector with a missing env in the path
src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":"replace","options":{"path":"${MISSING_CSV}"}}
root = make("t9b_csv_env", src)
p, r = load(root); store = Store(p.store_url(r))
res = run_source(store, p, r, p.sources[0])
print("=== csv path is ${MISSING_CSV}:", res.status, res.error)
store.close()
