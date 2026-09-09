import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from jsonserver import serve
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
import yaml, datetime
serve(8733)
U = "http://127.0.0.1:8733"

print("=== payload:true, append. poll1 has NO symbols, poll2 adds symbols: [A,B]")
src = {"id": "px", "to": ["raw.px"], "connector": "rest", "mode": "append",
       "options": {"url": f"{U}/list", "payload": True}}
root = make("t8b_payload", src)
p, r = load(root); store = Store(p.store_url(r))
print(" poll1:", run_source(store, p, r, p.sources[0]))
print(" cols:", store.table_info("raw.px").columns)
# now the user adds symbols
spec = yaml.safe_load((root/"qanat.yaml").read_text())
spec["sources"][0]["options"].update({"url": U + "/sym/{symbol}", "symbols": ["A","B"]})
(root/"qanat.yaml").write_text(yaml.safe_dump(spec))
p, r = load(root)
res = run_source(store, p, r, p.sources[0])
print(" poll2:", res.status, res.rows, res.error)
show(store, "raw.px")
print()
print("=== fetched_at timezone vs as-of")
print(" time_column:", store.time_column("raw.px"))
print(" max_time   :", store.max_time("raw.px"))
print(" utcnow     :", datetime.datetime.now(datetime.timezone.utc))
print(" localnow   :", datetime.datetime.now())
print(" duckdb TimeZone:", store.query("SELECT current_setting('TimeZone') AS tz").to_string())
utc_now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
print(f" read as_of {utc_now} (i.e. 'now' in UTC) ->", len(store.read("raw.px", as_of=utc_now)), "rows of", store.table_info("raw.px").rows)
store.close()
