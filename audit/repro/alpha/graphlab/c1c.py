from common import *
from qanat.runner import order, run_all
from qanat.project import load, validate

OK = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    script: steps/a.sql
  - id: feat_b
    from: [normalized.a]
    to: [normalized.b]
    script: steps/b.sql
"""
root = build("graphlab/c1c", bars(), ALPHA, extra_steps=OK)
(root/"steps/a.sql").write_text("SELECT date, symbol, close AS a FROM normalized__prices\n")
(root/"steps/b.sql").write_text("SELECT date, symbol, a AS b FROM normalized__a\n")
p,r,store,rep,res = run_pipeline(root)
show(res)
print("a sum:", store.query("SELECT sum(a) FROM normalized__a").iloc[0,0])
print("b sum:", store.query("SELECT sum(b) FROM normalized__b").iloc[0,0])
store.con.close()

# now the researcher edits feat_a to read normalized.b -> a cycle
y = (root/"qanat.yaml").read_text().replace(
  "  - id: feat_a\n    from: [normalized.prices]", "  - id: feat_a\n    from: [normalized.b]")
(root/"qanat.yaml").write_text(y)
(root/"steps/a.sql").write_text("SELECT date, symbol, b*10 AS a FROM normalized__b\n")
p,r = load(root)
print("VALIDATE after cycle:", rep_str(validate(p,r)).splitlines()[0])
print("order():", [s.id for s in order(p)])
from qanat.store import Store
store = Store(p.store_url(r))
res = run_all(store, p, r)
show(res)
print("a sum:", store.query("SELECT sum(a) FROM normalized__a").iloc[0,0])
print("b sum:", store.query("SELECT sum(b) FROM normalized__b").iloc[0,0])
res2 = run_all(store, p, r)
print("-- second pass --"); show(res2)
print("a sum:", store.query("SELECT sum(a) FROM normalized__a").iloc[0,0])
print("b sum:", store.query("SELECT sum(b) FROM normalized__b").iloc[0,0])
