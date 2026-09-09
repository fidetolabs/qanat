from common import *
from qanat.project import load
from qanat.runner import run_step
from qanat.store import Store
import yaml as Y

S = ("  - id: filt\n    from: [normalized.prices]\n    to: [normalized.filt]\n"
     "    script: steps/filt.sql\n    options: {sym: \"x') ; DROP TABLE normalized__prices ; SELECT (1\"}\n")
root = build("graphlab/c8b", bars(), ALPHA, extra_steps=S)
(root/"steps/filt.sql").write_text("SELECT * FROM normalized__prices WHERE symbol = '${sym}'\n")
p,r,store,rep,res = run_pipeline(root)
for x in res:
    if x.job_id=="filt": print("filt:", x.status, "|", (x.error or "")[:300])
print("tables after:", store.all_tables())
store.con.close()

# as_of collides with a user option named as_of
print("\n== an option literally named as_of, during a replay ==")
S2 = ("  - id: cut\n    from: [normalized.prices]\n    to: [normalized.cut]\n"
      "    script: steps/cut.sql\n    options: {as_of: '2024-01-10'}\n")
root = build("graphlab/c8c", bars(), ALPHA, extra_steps=S2)
(root/"steps/cut.sql").write_text("SELECT * FROM normalized__prices WHERE date <= DATE '${as_of}'\n")
p,r,store,rep,res = run_pipeline(root)
print("  normal run rows:", store.table_info("normalized.cut").rows, "(expect 30 = 3 syms x 10 days)")
res = run_step(store, p, r, p.job("cut"), as_of="2024-02-01")
print("  replay as_of=2024-02-01:", res.status, res.rows, "<- user option silently overridden by ctx.as_of")
