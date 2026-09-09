import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab")
from lab import *
from qanat.project import load
from qanat.runner import run_source
from qanat.store import Store
import pandas as pd, sqlite3, os

print("=== store.write() with an empty frame DOES clear (proving run_source never calls it)")
src = {"id":"bars","to":["raw.bars"],"connector":"csv","mode":"replace","options":{"path":"./seed/bars.csv"}}
root = make("t13_clear", src)
(root/"seed/bars.csv").write_text("date,symbol,close\n2024-01-01,A,100\n")
p, r = load(root); store = Store(p.store_url(r))
run_source(store, p, r, p.sources[0])
print("  rows before:", store.table_info("raw.bars").rows)
n = store.write("raw.bars", pd.DataFrame(columns=["date","symbol","close"]), mode="replace")
print("  store.write(empty) returned", n, "-> table rows now:", store.table_info("raw.bars").rows)
store.close()
print()

print("=== sql connector against sqlite")
db = "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab/srclab/t13.sqlite"
if os.path.exists(db): os.remove(db)
c = sqlite3.connect(db); c.execute("CREATE TABLE prices(ts TEXT, symbol TEXT, close REAL)")
c.executemany("INSERT INTO prices VALUES (?,?,?)", [("2024-01-01","A",100.0),("2024-01-02","A",101.5)])
c.commit(); c.close()
for label, o in [
  ("good query", {"dsn": f"sqlite:///{db}", "query": "SELECT ts, symbol, close FROM prices"}),
  ("bad table",  {"dsn": f"sqlite:///{db}", "query": "SELECT * FROM nope"}),
  ("bad dsn",    {"dsn": "sqlite:////nowhere/x.db", "query": "SELECT 1"}),
  ("no query",   {"dsn": f"sqlite:///{db}"}),
  ("${MISSING} dsn", {"dsn": "${NO_SUCH_DSN}", "query": "SELECT 1"}),
  ("bogus scheme", {"dsn": "notadb://x", "query": "SELECT 1"}),
]:
    s = {"id":"px","to":["raw.px"],"connector":"sql","mode":"replace","options":o}
    root = make("t13_"+"".join(ch if ch.isalnum() or ch=="_" else "_" for ch in label), s)
    p, r = load(root); store = Store(p.store_url(r))
    res = run_source(store, p, r, p.sources[0])
    print(f"  {label}: {res.status} rows={res.rows} err={(res.error or '').splitlines()[0] if res.error else None}")
    if res.status=="ok": show(store,"raw.px")
    store.close()
