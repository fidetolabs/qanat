from common import *
from qanat.project import load
from qanat.scheduler import Scheduler
import os, time

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
"""
root = build("graphlab/c5b", bars(), ALPHA, extra_steps=S)
(root/"steps/a.py").write_text('''
import os
def run(ctx):
    px = ctx.read("normalized.prices")
    if os.environ.get("EMPTY"): px = px.iloc[0:0]
    return px.assign(a=px["close"])[["date","symbol","a"]]
''')
(root/"steps/b.py").write_text('''
def run(ctx):
    px = ctx.read("normalized.a")
    return px.assign(b=px["a"]*2)[["date","symbol","b"]]
''')
p,r,store,rep,res = run_pipeline(root); show(res)
def rows(t):
    i=store.table_info(t); return i.rows if i else None
print("  a=%s b=%s" % (rows("normalized.a"), rows("normalized.b")))

print("\n== upstream now legitimately computes 0 rows, cascade via wake ==")
os.environ["EMPTY"]="1"
sch = Scheduler(store,p,r)
print("  wake:", sch.wake(["normalized.prices"]))
sch.quiet(30); time.sleep(0.6); sch.quiet(30)
print("  a=%s b=%s   <-- b should have been cleared too" % (rows("normalized.a"), rows("normalized.b")))
for e in store.recent_events(5): print("   ", e)
