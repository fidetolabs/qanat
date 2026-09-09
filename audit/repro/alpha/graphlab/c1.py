from common import *
from qanat.runner import order

# 3-deep chain inside the SAME features stage, declared in REVERSE dep order.
CHAIN = """  - id: feat_c
    from: [normalized.b]
    to: [normalized.c]
    script: steps/c.sql
  - id: feat_b
    from: [normalized.a]
    to: [normalized.b]
    script: steps/b.sql
  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    script: steps/a.sql
"""
ALPHA_C = '''
def run(ctx):
    import pandas as pd
    px = ctx.read("normalized.c")
    last = px[px["date"] == px["date"].max()]
    n = len(last)
    return pd.DataFrame({"date": last["date"].values, "symbol": last["symbol"].values,
                         "weight": [1.0/n]*n})
'''
root = build("graphlab/c1_rev", bars(), ALPHA_C, extra_steps=CHAIN)
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS a FROM normalized__prices\n")
(root/"steps/b.sql").write_text("SELECT date, symbol, a*2 AS b FROM normalized__a\n")
(root/"steps/c.sql").write_text("SELECT date, symbol, b+1 AS c FROM normalized__b\n")
# alpha reads normalized.c
y = (root/"qanat.yaml").read_text().replace(
    "  - id: alpha_h\n    from: [normalized.prices]", "  - id: alpha_h\n    from: [normalized.c]")
(root/"qanat.yaml").write_text(y)

from qanat.project import load, validate
p, r = load(root)
print("VALIDATE:", rep_str(validate(p, r)))
print("FILE ORDER:", [s.id for s in p.steps])
print("order():   ", [s.id for s in order(p)])
p2, r2, store, rep, res = run_pipeline(root)
show(res)
for t in ["normalized.a","normalized.b","normalized.c","weights.h"]:
    i = store.table_info(t); print(f"  {t}: {i.rows if i else 'MISSING'} rows")
