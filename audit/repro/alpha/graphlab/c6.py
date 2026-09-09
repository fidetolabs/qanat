from common import *
from qanat.project import load, validate
from qanat import editor
import yaml as Y

S = """  - id: cross
    from: [raw.bars]
    to: [normalized.derived]
    script: steps/cross.sql
"""
root = build("graphlab/c6", bars(), ALPHA, extra_steps=S)
(root/"steps/cross.sql").write_text("SELECT date, symbol, close FROM raw__bars\n")
p,r,store,rep,res = run_pipeline(root); show(res); store.con.close()

p,r = load(root)
print("\nstages before:", [(s.id,s.kind) for s in p.stages])
print("-- add_stage('interim') with no `before` --")
w = editor.add_stage(p, r, "interim", kind="features")
print("   stages:", [(s.id,s.kind) for s in p.stages])
print("   validate:", validate(p,r).errors or "none")

print("\n-- add_stage('mid', before='normalized') : between raw and normalized --")
p,r = load(root)
editor.add_stage(p, r, "mid", kind="features", before="normalized")
print("   stages:", [(s.id,s.kind) for s in p.stages])
print("   existing step 'cross' (raw->normalized) still valid?", validate(p,r).errors or "none")

print("\n-- add_stage AFTER weights (before='pnl') --")
p,r = load(root)
try:
    editor.add_stage(p, r, "post", kind="features", before="pnl")
    print("   ACCEPTED. stages:", [(s.id,s.kind) for s in p.stages])
except Exception as e:
    print("   REJECTED:", type(e).__name__, e)

print("\n-- add_stage at the very end (before=nonexistent -> append) --")
p,r = load(root)
try:
    editor.add_stage(p, r, "post2", kind="features", before=None)
    print("   result stages:", [(s.id,s.kind) for s in p.stages])
except Exception as e:
    print("   REJECTED:", type(e).__name__, e)

print("\n-- two weights stages (raw yaml) --")
p,r = load(root)
d = Y.safe_load((root/"qanat.yaml").read_text())
d["stages"] = [{"id":"raw","kind":"raw"},{"id":"normalized","kind":"features"},
               {"id":"weights","kind":"weights"},{"id":"weights2","kind":"weights"},{"id":"pnl","kind":"pnl"}]
open(root/"q2.yaml","w").write(Y.safe_dump(d,sort_keys=False))
p2,_ = load(root/"q2.yaml")
print("   ", validate(p2,r).errors)

print("\n-- zero weights stages --")
d["stages"] = [{"id":"raw","kind":"raw"},{"id":"normalized","kind":"features"}]
d["steps"] = [s for s in d["steps"] if s["id"] in ("normalize","cross")]
d.pop("backtest",None)
open(root/"q3.yaml","w").write(Y.safe_dump(d,sort_keys=False))
p3,_ = load(root/"q3.yaml")
print("   ", validate(p3,r).errors)
