from common import *
from qanat.project import load, validate
from qanat.plan import plan
from qanat.store import Store
from qanat.runner import run_all

def pp(pl):
    for k in ("creates","orphans","adds","updates","removes"):
        for c in getattr(pl,k):
            print(f"   {c.action:7s} {c.target:22s} {c.note} {c.details or ''}")
    print(f"   unchanged={pl.unchanged} first_run={pl.first_run}")

S = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.mom]
    script: steps/a.sql
  - id: feat_b
    from: [normalized.mom]
    to: [normalized.zmom]
    script: steps/b.sql
"""
root = build("graphlab/c4", bars(), ALPHA, extra_steps=S)
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS mom FROM normalized__prices\n")
(root/"steps/b.sql").write_text("SELECT date, symbol, mom*1.0 AS zmom FROM normalized__mom\n")
p,r,store,rep,res = run_pipeline(root); show(res)
print("\n== plan, clean ==") ; pp(plan(p,r,store))
store.con.close()

print("\n== rename normalized.mom -> normalized.momentum (both sides) ==")
y=(root/"qanat.yaml").read_text().replace("normalized.mom]","normalized.momentum]").replace("[normalized.mom]","[normalized.momentum]")
(root/"qanat.yaml").write_text(y)
(root/"steps/b.sql").write_text("SELECT date, symbol, momentum*1.0 AS zmom FROM normalized__momentum\n")
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS momentum FROM normalized__prices\n")
p,r = load(root); print("check:", validate(p,r).errors or "none")
store = Store(p.store_url(r)); pl = plan(p,r,store); pp(pl)
print("   stale():", sorted(pl.stale(p, store)))
print("   old table still there?", store.exists("normalized.mom"), store.table_info("normalized.mom").rows)
print("   all tables:", store.all_tables())
store.con.close()

print("\n== now DELETE feat_a (feat_b still reads normalized.momentum) ==")
import yaml as Y
d = Y.safe_load((root/"qanat.yaml").read_text())
d["steps"] = [s for s in d["steps"] if s["id"]!="feat_a"]
(root/"qanat.yaml").write_text(Y.safe_dump(d, sort_keys=False))
p,r = load(root)
print("check errors:", validate(p,r).errors or "NONE")
