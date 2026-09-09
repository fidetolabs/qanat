from common import *
from qanat.project import load, validate
from qanat import editor
import yaml as Y

root = build("graphlab/c6b", bars(), ALPHA)
p,r,store,rep,res = run_pipeline(root); store.con.close()
p,r = load(root)
editor.add_stage(p, r, "post", kind="features", before="pnl")
print("stages:", [(s.id,s.kind) for s in p.stages])
d = Y.safe_load((root/"qanat.yaml").read_text())
d["steps"].append({"id":"stack","from":["weights.h"],"to":["post.doubled"],"script":"steps/stack.sql"})
d["steps"].append({"id":"alpha_g","from":["post.doubled"],"to":["weights.g"],"script":"steps/g.py","universe":"u"})
(root/"qanat.yaml").write_text(Y.safe_dump(d,sort_keys=False))
(root/"steps/stack.sql").write_text("SELECT date, symbol, weight*2 AS weight FROM weights__h\n")
(root/"steps/g.py").write_text('''
def run(ctx):
    d = ctx.read("post.doubled")
    d = d.copy(); d["weight"] = d["weight"]/d["weight"].abs().sum()
    return d
''')
p,r = load(root)
v = validate(p,r)
print("VALIDATE:", v.errors or "NONE  <-- alpha_g is built from alpha_h's weights")
print("alphas:", p.alphas)
p2,r2,store,rep,res = run_pipeline(root); show(res)
from qanat.plan import plan
print("weights.g rows:", store.table_info("weights.g").rows)
print("weights.g:\n", store.read("weights.g"))
