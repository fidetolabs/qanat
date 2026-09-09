import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

print("=== NULL inside a key column, append + key=[date,symbol], polled twice")
CSV = "date,symbol,close\n2024-01-01,A,100\n2024-01-01,,200\n2024-01-02,A,101\n"
src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":"append",
       "key":["date","symbol"],"options":{"path":"./seed/bars.csv"}}
root = make("t10_nullkey", src)
(root/"seed/bars.csv").write_text(CSV)
p, r = load(root); store = Store(p.store_url(r))
for i in (1,2,3):
    res = run_source(store, p, r, p.sources[0])
    print(f" poll{i}: {res.status} rows={res.rows} -> table rows={store.table_info('raw.bars').rows}")
show(store)
store.close()
print()

print("=== bad mode value")
import pydantic
for m in ["upsert", "Append", "APPEND"]:
    src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":m,"options":{"path":"./seed/bars.csv"}}
    root = make("t10_mode_"+m, src)
    (root/"seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,100\n")
    try:
        p, r = load(root); print(f"  mode={m!r} -> accepted as {p.sources[0].mode!r}")
    except Exception as e:
        print(f"  mode={m!r} -> {type(e).__name__}: {str(e).splitlines()[0]} / {str(e).splitlines()[2] if len(str(e).splitlines())>2 else ''}")
print()

print("=== two sources writing the SAME table")
import yaml
root = make("t10_two", {"id":"a","to":["raw.bars"],"connector":"csv","mode":"append","options":{"path":"./seed/a.csv"}})
spec = yaml.safe_load((root/"qanat.yaml").read_text())
spec["sources"].append({"id":"b","to":["raw.bars"],"connector":"csv","mode":"append","options":{"path":"./seed/b.csv"}})
(root/"qanat.yaml").write_text(yaml.safe_dump(spec))
(root/"seed/a.csv").write_text("date,symbol,close\n2024-01-01,A,100\n")
(root/"seed/b.csv").write_text("date,close,symbol\n2024-01-01,200,B\n")   # different order!
p, r = load(root)
from qanat.project import validate
rep = validate(p, r)
print("  validate errors:", rep.errors, "warnings:", rep.warnings)
store = Store(p.store_url(r))
for s in p.sources:
    print(f"  source {s.id}:", run_source(store, p, r, s))
show(store)
store.close()
