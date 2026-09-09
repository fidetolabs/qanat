from common import *
from qanat.runner import order
from qanat.project import load, validate

CYC = """  - id: feat_a
    from: [normalized.b]
    to: [normalized.a]
    script: steps/a.sql
  - id: feat_b
    from: [normalized.a]
    to: [normalized.b]
    script: steps/b.sql
"""
root = build("graphlab/c1b_cycle", bars(), ALPHA, extra_steps=CYC)
(root/"steps/a.sql").write_text("SELECT date, symbol, b AS a FROM normalized__b\n")
(root/"steps/b.sql").write_text("SELECT date, symbol, a AS b FROM normalized__a\n")
p, r = load(root)
print("VALIDATE:", rep_str(validate(p, r)))
print("order():", [s.id for s in order(p)])
p2,r2,store,rep,res = run_pipeline(root)
show(res)
