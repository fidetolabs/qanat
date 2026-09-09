import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store

# same key twice in ONE batch, with an explicit revision timestamp so we can see
# which one "should" win. The newest correction (rev 2) is listed FIRST in the file,
# which is what a feed that returns newest-first does.
CSV = ("date,symbol,close,rev,fetched\n"
       "2024-01-01,A,999,2,2024-01-01T12:00:00\n"   # newest correction
       "2024-01-01,A,100,1,2024-01-01T09:00:00\n"   # older value
       "2024-01-02,A,101,1,2024-01-02T09:00:00\n")
src = {"id": "bars", "to": ["raw.bars"], "connector": "csv", "mode": "append",
       "key": ["date", "symbol"], "options": {"path": "./seed/bars.csv"}}
root = make("t4_dup", src)
(root / "seed/bars.csv").write_text(CSV)
p, r = load(root); store = Store(p.store_url(r))
res = run_source(store, p, r, p.sources[0])
print("poll1:", res)
show(store)
print("events:"); events(store, 3)
store.close()
