import sys, os; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from jsonserver import serve
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
from qanat.sources.rest import expand
serve(8734)
U = "http://127.0.0.1:8734"

print("--- expand() unit checks")
os.environ["MY_TOKEN"] = "SECRET"
for v in ["Bearer ${MY_TOKEN}", "Bearer ${NOT_SET}", "${my_token}", "${NOT_SET}-tail",
          "http://h/${NOT_SET}/px", "${A_B9}"]:
    print(f"  {v!r:28} -> {expand(v)!r}")
print()

def run(label, opts, env=None):
    if env:
        for k, v in env.items():
            os.environ[k] = v
    name = "t9_" + label.replace(" ", "_")
    src = {"id": "px", "to": ["raw.px"], "connector": "rest", "mode": "replace", "options": opts}
    root = make(name, src)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"=== {label}: status={res.status} rows={res.rows}")
    if res.error: print("    err:", res.error.replace("\n", " | "))
    if res.status == "ok": show(store, "raw.px")
    store.close(); print()

os.environ["TOK"] = "SECRET"
os.environ.pop("MISSING_TOK", None)
run("header env SET", {"url": f"{U}/needauth", "headers": {"Authorization": "Bearer ${TOK}"}})
run("header env MISSING", {"url": f"{U}/needauth", "headers": {"Authorization": "Bearer ${MISSING_TOK}"}})
run("what the server sees, env MISSING", {"url": f"{U}/echoauth", "headers": {"Authorization": "Bearer ${MISSING_TOK}"}})
run("url env MISSING", {"url": U + "/${MISSING_HOSTPART}list"})
run("param env MISSING", {"url": f"{U}/echoauth", "params": {"apikey": "${MISSING_TOK}", "iv": "1d"}})
os.environ.pop("MISSING_URL", None)
run("whole url is missing env", {"url": "${MISSING_URL}"})
