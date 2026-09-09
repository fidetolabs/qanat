from common import *
from qanat.runner import run_all
from qanat.project import load
from qanat.store import Store

S = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    script: steps/a.py
  - id: feat_b
    from: [normalized.a]
    to: [normalized.b]
    script: steps/b.sql
"""
root = build("graphlab/c3b", bars(), ALPHA, extra_steps=S)
(root/"steps/a.py").write_text('''
import os
def run(ctx):
    px = ctx.read("normalized.prices")
    if os.environ.get("BOOM"):
        raise RuntimeError("the vendor file was truncated")
    return px.assign(a=px["close"])[["date","symbol","a"]]
''')
(root/"steps/b.sql").write_text("SELECT date, symbol, sum(a) OVER () AS b FROM normalized__a\n")
p,r,store,rep,res = run_pipeline(root); show(res)
print("b first value:", store.query("SELECT b FROM normalized__b LIMIT 1").iloc[0,0])
store.con.close()
import os; os.environ["BOOM"]="1"
p,r = load(root); store = Store(p.store_url(r))
res = run_all(store,p,r); print("-- with feat_a failing --"); show(res)
print("b first value:", store.query("SELECT b FROM normalized__b LIMIT 1").iloc[0,0])
