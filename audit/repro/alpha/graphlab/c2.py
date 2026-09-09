from common import *
from qanat.runner import order
from qanat.project import load, validate

# --- 2a: one .py step writing 2 tables; one step reading 4 tables across 2 stages
STEPS = """  - id: fanout
    from: [normalized.prices]
    to: [normalized.f1, normalized.f2]
    script: steps/fanout.py
  - id: fanin
    from: [raw.bars, normalized.prices, normalized.f1, normalized.f2]
    to: [normalized.wide]
    script: steps/fanin.py
"""
root = build("graphlab/c2", bars(), ALPHA, extra_steps=STEPS)
(root/"steps/fanout.py").write_text('''
def run(ctx):
    px = ctx.read("normalized.prices")
    a = px.assign(f1=px["close"]*2)[["date","symbol","f1"]]
    b = px.assign(f2=px["close"]*3)[["date","symbol","f2"]]
    return {"normalized.f1": a, "f2": b}   # mix of qualified and bare keys
''')
(root/"steps/fanin.py").write_text('''
def run(ctx):
    import functools
    dfs = [ctx.read(t) for t in ("raw.bars","normalized.prices","normalized.f1","normalized.f2")]
    ctx.log("read %d tables, rows=%s" % (len(dfs), [len(d) for d in dfs]))
    m = dfs[1]
    for d in dfs[2:]:
        m = m.merge(d, on=["date","symbol"])
    return m
''')
p,r = load(root)
print("VALIDATE 2a:", rep_str(validate(p,r)).splitlines()[0])
print("order():", [s.id for s in order(p)])
p,r,store,rep,res = run_pipeline(root)
show(res)
for t in ["normalized.f1","normalized.f2","normalized.wide"]:
    i=store.table_info(t); print(f"  {t}: {i.rows if i else 'MISSING'} rows cols={[c[0] for c in i.columns] if i else None}")
store.con.close()

# --- 2b: a .sql step declaring 2 writes
print("\n=== 2b: .sql with two writes ===")
S2 = """  - id: twosql
    from: [normalized.prices]
    to: [normalized.x, normalized.y]
    script: steps/two.sql
"""
root2 = build("graphlab/c2b", bars(), ALPHA, extra_steps=S2)
(root2/"steps/two.sql").write_text("SELECT * FROM normalized__prices\n")
p2,r2 = load(root2)
print(rep_str(validate(p2,r2)))
