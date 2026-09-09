from common import *
from qanat.project import load, validate
from qanat.plan import plan, _replay_writes
from qanat.store import Store
import yaml as Y

# 7a asymmetry: features stage after weights, with and without a pnl stage
root = build("graphlab/c7a", bars(), ALPHA)
d = Y.safe_load((root/"qanat.yaml").read_text())
withpnl = [{"id":"raw","kind":"raw"},{"id":"normalized","kind":"features"},
           {"id":"weights","kind":"weights"},{"id":"post","kind":"features"},{"id":"pnl","kind":"pnl"}]
nopnl   = withpnl[:-1]
for tag, st in (("with pnl stage", withpnl), ("without pnl stage", nopnl)):
    d["stages"] = st
    (root/"qq.yaml").write_text(Y.safe_dump(d,sort_keys=False))
    pp,_ = load(root/"qq.yaml")
    print(f"  features stage after weights, {tag}: {validate(pp, root).errors or 'NO ERROR'}")

# 7b: step writing into pnl
print("\n== step writing into the pnl stage ==")
S = """  - id: cheat
    from: [weights.h]
    to: [pnl.h]
    script: steps/cheat.sql
"""
root = build("graphlab/c7b", bars(), ALPHA, extra_steps=S)
(root/"steps/cheat.sql").write_text("SELECT * FROM weights__h\n")
p,r = load(root); print("  ", validate(p,r).errors)

# 7c: plan/prune vs real backtest pnl tables
print("\n== pnl tables written by a real backtest ==")
root = build("graphlab/c7c", bars(), ALPHA)
p,r,store,rep,res = run_pipeline(root); show(res)
bt = backtest(store, p, r, "2024-01-05", "2024-02-09")
print("  backtest ok:", getattr(bt,'alpha',None) or type(bt).__name__)
print("  all tables:", store.all_tables())
pl = plan(p,r,store)
print("  _replay_writes:", _replay_writes(p, store))
print("  orphans:", [(c.target,c.note) for c in pl.orphans])
store.con.close()

print("\n-- now RENAME the alpha step (alpha_h -> alpha_hh), pnl table unchanged --")
d = Y.safe_load((root/"qanat.yaml").read_text())
for s in d["steps"]:
    if s["id"]=="alpha_h": s["id"]="alpha_hh"; s["to"]=["weights.hh"]
(root/"qanat.yaml").write_text(Y.safe_dump(d,sort_keys=False))
p,r = load(root); store=Store(p.store_url(r))
pl = plan(p,r,store)
print("  _replay_writes:", _replay_writes(p, store))
print("  orphans:", [(c.target,c.note) for c in pl.orphans])
