from common import *
from qanat.project import load, validate

def case(tag, body, tos):
    S = f"""  - id: bad
    from: [normalized.prices]
    to: [{tos}]
    script: steps/bad.py
"""
    root = build(f"graphlab/c3_{tag}", bars(), ALPHA, extra_steps=S)
    (root/"steps/bad.py").write_text(body)
    p,r = load(root)
    v = validate(p,r)
    print(f"--- {tag} ---")
    print("  check errors:", v.errors or "none")
    p,r,store,rep,res = run_pipeline(root)
    for x in res:
        if x.job_id=="bad": print("  runtime:", x.status, "|", x.error)
    for t in ("normalized.p","normalized.q","normalized.undeclared"):
        i = store.table_info(t)
        if i: print(f"  table {t} EXISTS with {i.rows} rows")
    store.con.close()

# declares 2 writes, produces only 1
case("missing", '''
def run(ctx):
    px = ctx.read("normalized.prices")
    return {"p": px}
''', "normalized.p, normalized.q")

# produces a table it did not declare (plus the declared one)
case("extra", '''
def run(ctx):
    px = ctx.read("normalized.prices")
    return {"p": px, "normalized.undeclared": px}
''', "normalized.p")

# declares 2, returns a bare DataFrame
case("bareframe", '''
def run(ctx):
    return ctx.read("normalized.prices")
''', "normalized.p, normalized.q")
