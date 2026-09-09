from common import *
from qanat.project import load, validate
from qanat.plan import plan, job_spec
from qanat.store import Store
import yaml as Y

def pp(pl):
    for k in ("creates","orphans","adds","updates","removes"):
        for c in getattr(pl,k):
            print(f"   {c.action:7s} {c.target:20s} {c.note} {c.details or ''}")
    print(f"   unchanged={pl.unchanged}")

S = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.mom]
    script: steps/a.sql
  - id: feat_b
    from: [normalized.mom]
    to: [normalized.zmom]
    script: steps/b.sql
"""
ALPHA_Z = ALPHA.replace('normalized.prices','normalized.zmom')
root = build("graphlab/c4b", bars(), ALPHA_Z, extra_steps=S)
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS mom FROM normalized__prices\n")
(root/"steps/b.sql").write_text("SELECT date, symbol, mom*1.0 AS zmom FROM normalized__mom\n")
y=(root/"qanat.yaml").read_text().replace("  - id: alpha_h\n    from: [normalized.prices]","  - id: alpha_h\n    from: [normalized.zmom]")
(root/"qanat.yaml").write_text(y)
p,r,store,rep,res = run_pipeline(root); show(res); store.con.close()

print("\n-- edit feat_a script only --")
(root/"steps/a.sql").write_text("SELECT date, symbol, close*2 AS mom FROM normalized__prices\n")
p,r = load(root); store=Store(p.store_url(r)); pl=plan(p,r,store); pp(pl)
print("   stale():", sorted(pl.stale(p, store)))
store.con.close()
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS mom FROM normalized__prices\n")

print("\n-- change ONLY `rebalance:` on the alpha step --")
d = Y.safe_load((root/"qanat.yaml").read_text())
for s in d["steps"]:
    if s["id"]=="alpha_h": s["rebalance"]="21d"
(root/"qanat.yaml").write_text(Y.safe_dump(d, sort_keys=False))
p,r = load(root); store=Store(p.store_url(r)); pl=plan(p,r,store); pp(pl)
print("   stale():", sorted(pl.stale(p,store)))
print("   job_spec(alpha_h):", job_spec(p.job("alpha_h"), r))
store.con.close()

print("\n-- change ONLY `decay:` and `when:` --")
d = Y.safe_load((root/"qanat.yaml").read_text())
for s in d["steps"]:
    if s["id"]=="alpha_h": s["decay"]=5; s["when"]=["normalized.zmom"]
(root/"qanat.yaml").write_text(Y.safe_dump(d, sort_keys=False))
p,r = load(root); store=Store(p.store_url(r)); pl=plan(p,r,store); pp(pl)
print("   empty plan?", pl.empty)
store.con.close()

print("\n-- change ONLY source `key:` and `mode:` --")
d = Y.safe_load((root/"qanat.yaml").read_text())
d["sources"][0]["key"]=["date","symbol"]
(root/"qanat.yaml").write_text(Y.safe_dump(d, sort_keys=False))
p,r = load(root); store=Store(p.store_url(r)); pl=plan(p,r,store); pp(pl)
print("   empty plan?", pl.empty)
