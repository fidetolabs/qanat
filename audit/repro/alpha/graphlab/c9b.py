from common import *
from qanat.project import load, validate
from qanat.runner import order, run_all
from qanat.store import Store

# sneak_sql declared BEFORE feat_a, and it really depends on normalized.a
S = """  - id: sneak_sql
    from: [normalized.prices]
    to: [normalized.combo]
    script: steps/sneak.sql
  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    script: steps/a.sql
"""
root = build("graphlab/c9b", bars(), ALPHA, extra_steps=S)
(root/"steps/a.sql").write_text("SELECT date, symbol, close*7 AS a FROM normalized__prices\n")
(root/"steps/sneak.sql").write_text(
  "SELECT p.date, p.symbol, x.a FROM normalized__prices p JOIN normalized__a x USING (date, symbol)\n")
p,r = load(root)
v = validate(p,r)
print("check errors:", v.errors or "none")
print("check warnings:", [w for w in v.warnings if "never read" in w])
print("order():", [s.id for s in order(p)])
p,r,store,rep,res = run_pipeline(root); show(res)
store.con.close()

print("\n-- run 2 (normalized.a now exists from run 1) --")
p,r = load(root); store=Store(p.store_url(r))
show(run_all(store,p,r))
print("   combo a[0] =", store.query("SELECT a FROM normalized__combo LIMIT 1").iloc[0,0])
store.con.close()

print("\n-- researcher edits feat_a to *7 -> *1000, reruns: combo uses the OLD a --")
(root/"steps/a.sql").write_text("SELECT date, symbol, close*1000 AS a FROM normalized__prices\n")
p,r = load(root); store=Store(p.store_url(r))
show(run_all(store,p,r))
print("   normalized.a[0]  =", store.query("SELECT a FROM normalized__a LIMIT 1").iloc[0,0])
print("   combo    a[0]  =", store.query("SELECT a FROM normalized__combo LIMIT 1").iloc[0,0], " <- computed from the pre-edit a")
