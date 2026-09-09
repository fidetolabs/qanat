from common import *
from qanat.project import load, validate
from qanat.runner import order

S = """  - id: alpha_g
    from: [normalized.prices]
    to: [weights.h]
    script: steps/g.py
    universe: u
"""
root = build("graphlab/c11", bars(), ALPHA, extra_steps=S)
(root/"steps/g.py").write_text('''
def run(ctx):
    import pandas as pd
    px = ctx.read("normalized.prices")
    last = px[px["date"]==px["date"].max()]
    w = [0.5,0.3,0.2][:len(last)]
    return pd.DataFrame({"date":last["date"].values,"symbol":last["symbol"].values,"weight":w})
''')
p,r = load(root)
print("two alpha steps both write weights.h")
print(" check errors:", validate(p,r).errors or "NONE")
print(" p.alphas:", p.alphas, " <- two alphas, one table")
print(" order():", [s.id for s in order(p)])
p,r,store,rep,res = run_pipeline(root); show(res)
print(store.read("weights.h"))
