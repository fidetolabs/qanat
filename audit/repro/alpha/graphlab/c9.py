from common import *
from qanat.project import load, validate, edges
from qanat.runner import order, run_all
from qanat.plan import plan
from qanat.store import Store

S = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    script: steps/a.sql
  - id: sneak_py
    from: [normalized.prices]
    to: [normalized.sneak_py]
    script: steps/sneak.py
  - id: sneak_sql
    from: [normalized.prices]
    to: [normalized.sneak_sql]
    script: steps/sneak.sql
"""
root = build("graphlab/c9", bars(), ALPHA, extra_steps=S)
(root/"steps/a.sql").write_text("SELECT date, symbol, close*7 AS a FROM normalized__prices\n")
(root/"steps/sneak.py").write_text('''
def run(ctx):
    try:
        ctx.read("normalized.a")
    except Exception as e:
        ctx.log("ctx.read refused: %s: %s" % (type(e).__name__, e))
        print("   ctx.read ->", type(e).__name__, ":", e)
    got = ctx.sql("SELECT date, symbol, a AS s FROM normalized__a")   # not declared
    print("   ctx.sql got", len(got), "rows from the undeclared normalized.a")
    return got
''')
# a .sql step that joins an undeclared table
(root/"steps/sneak.sql").write_text(
  "SELECT p.date, p.symbol, p.close, x.a\n"
  "FROM normalized__prices p JOIN normalized__a x USING (date, symbol)\n")

p,r = load(root)
print("check:", validate(p,r).errors or "none")
print("declared edges (what the console draws):")
for a,b,s in edges(p): print(f"   {a} -> {b}   [{s}]")
print("order():", [s.id for s in order(p)])
p,r,store,rep,res = run_pipeline(root); show(res)
for t in ("normalized.sneak_py","normalized.sneak_sql"):
    i=store.table_info(t); print(f"  {t}: {i.rows if i else 'MISSING'} rows cols={[c[0] for c in i.columns] if i else None}")
store.con.close()

print("\n== does staleness reach the sneaky consumers? edit feat_a's script ==")
(root/"steps/a.sql").write_text("SELECT date, symbol, close*999 AS a FROM normalized__prices\n")
p,r = load(root); store = Store(p.store_url(r)); pl = plan(p,r,store)
print("   updates:", [(c.target,c.details) for c in pl.updates])
print("   stale():", sorted(pl.stale(p,store)), "<- sneak_py/sneak_sql outputs are NOT listed")
