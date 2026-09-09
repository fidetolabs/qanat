from common import *
from qanat.project import load, validate, edges
from qanat.runner import order
from qanat.plan import plan

print("=== A: two steps writing the SAME table ===")
S = """  - id: dup1
    from: [normalized.prices]
    to: [normalized.dup]
    script: steps/d1.sql
  - id: dup2
    from: [normalized.prices]
    to: [normalized.dup]
    script: steps/d2.sql
  - id: reader
    from: [normalized.dup]
    to: [normalized.out]
    script: steps/rd.sql
"""
root = build("graphlab/c10a", bars(), ALPHA, extra_steps=S)
(root/"steps/d1.sql").write_text("SELECT date, symbol, 1 AS v FROM normalized__prices\n")
(root/"steps/d2.sql").write_text("SELECT date, symbol, 2 AS v FROM normalized__prices\n")
(root/"steps/rd.sql").write_text("SELECT date, symbol, v FROM normalized__dup\n")
p,r = load(root)
print(" check:", validate(p,r).errors or "NONE")
print(" producers():", p.producers())
print(" order():", [s.id for s in order(p)])
p,r,store,rep,res = run_pipeline(root); show(res)
print(" normalized.dup v =", store.query("SELECT DISTINCT v FROM normalized__dup").v.tolist(), "<- dup2 won")
print(" edges drawn:")
for a,b,s in edges(p):
    if "dup" in b or "dup" in a: print("   ", a,"->",b,"[",s,"]")
pl = plan(p,r,store)
print(" plan orphans:", [c.target for c in pl.orphans], " creates:", [c.target for c in pl.creates])
store.con.close()

print("\n=== B: a SOURCE and a STEP writing the same table ===")
S2 = """  - id: overwrite
    from: [normalized.prices]
    to: [raw.bars2]
    script: steps/ow.sql
"""
root = build("graphlab/c10b", bars(), ALPHA, extra_steps=S2)
(root/"steps/ow.sql").write_text("SELECT * FROM normalized__prices\n")
import yaml as Y
d = Y.safe_load((root/"qanat.yaml").read_text())
d["sources"].append({"id":"bars2","to":["normalized.prices"],"connector":"csv","mode":"replace",
                     "options":{"path":"./seed/bars.csv"}})
(root/"qanat.yaml").write_text(Y.safe_dump(d,sort_keys=False))
p,r = load(root)
print(" source bars2 and step normalize both write normalized.prices")
print(" check:", validate(p,r).errors or "NONE")
print(" producers()['normalized.prices'] =", p.producers()["normalized.prices"])
print(" order():", [s.id for s in order(p)])

print("\n=== C: step reads and writes the same table (self loop) ===")
S3 = """  - id: selfy
    from: [normalized.prices]
    to: [normalized.prices]
    script: steps/s.sql
"""
root = build("graphlab/c10c", bars(), ALPHA, extra_steps=S3)
(root/"steps/s.sql").write_text("SELECT * FROM normalized__prices\n")
p,r = load(root); print(" check:", validate(p,r).errors)
