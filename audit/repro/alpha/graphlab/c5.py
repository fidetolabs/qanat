from common import *
from qanat.project import load, validate
from qanat.scheduler import Scheduler
from qanat.store import Store
from qanat.runner import run_source
import time, os

S = """  - id: feat_a
    from: [normalized.prices]
    to: [normalized.a]
    when: [normalized.prices]
    script: steps/a.py
  - id: feat_b
    from: [normalized.a]
    to: [normalized.b]
    when: [normalized.a]
    script: steps/b.py
  - id: feat_c
    from: [normalized.b]
    to: [normalized.c]
    when: [normalized.b]
    script: steps/c.py
"""
root = build("graphlab/c5", bars(), ALPHA, extra_steps=S)
for n,src,col in (("a","normalized.prices","close"),("b","normalized.a","a"),("c","normalized.b","b")):
    (root/f"steps/{n}.py").write_text(f'''
import os
def run(ctx):
    if os.environ.get("BOOM_{n.upper()}"): raise RuntimeError("step {n} blew up")
    px = ctx.read("{src}")
    return px.assign({n}=px["{col}"])[["date","symbol","{n}"]]
''')
p,r = load(root); print("check:", validate(p,r).errors or "none")
p,r,store,rep,res = run_pipeline(root); show(res)

print("\n== drop a,b,c then wake(normalized.prices) ==")
for t in ("normalized.a","normalized.b","normalized.c"): store.drop(t)
print("  tables:", store.all_tables())
sch = Scheduler(store,p,r)
print("  status():", {k:v for k,v in sch.status().items()})
woken = sch.wake(["normalized.prices"])
print("  wake returned:", woken)
sch.quiet(30)
time.sleep(0.5); sch.quiet(30)
print("  tables after cascade:", store.all_tables())

print("\n== now break feat_b and re-cascade ==")
for t in ("normalized.a","normalized.b","normalized.c"): store.drop(t)
os.environ["BOOM_B"]="1"
woken = sch.wake(["normalized.prices"]); print("  wake:", woken)
sch.quiet(30); time.sleep(0.7); sch.quiet(30)
print("  tables:", store.all_tables())
ev = store.query("SELECT level, job_id, message FROM _qanat_events ORDER BY rowid DESC LIMIT 8") if False else store.recent_events(10)
for e in ev: print("   ", e)
