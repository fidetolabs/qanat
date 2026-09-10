"""H11: 'Two runs with the same digest are the same question, so a different
answer means something moved underneath.' Is that true?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
bars = per_symbol_drift(CAL, {"A": 0.002, "B": 0.0005})

BASE = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    n = int(ctx.options.get("lookback", 5))
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
RANDOM = '''
import pandas as pd, random
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    pick = random.choice(["A","B"])
    return pd.DataFrame([{"symbol":pick,"weight":1.0,"as_of":df["date"].max()}])
'''
root = build("repro", bars, BASE, rebalance="5d")
p, r, store, rep, res = run_pipeline(root)

print("=" * 84)
print("H11a  same project, same window, run twice")
print("=" * 84)
a = backtest(store, p, r, "2024-02-01", "2024-05-01", seed=7)
b = backtest(store, p, r, "2024-02-01", "2024-05-01", seed=7)
print(f"  digest A {a.digest}  net {a.totals['net']:.10f}")
print(f"  digest B {b.digest}  net {b.totals['net']:.10f}")
print(f"  -> {'same question, same answer' if a.digest==b.digest and abs(a.totals['net']-b.totals['net'])<1e-12 else 'DIFFERS'}")

print()
print("=" * 84)
print("H11b  edit the alpha SCRIPT, rerun. Does the digest notice?")
print("=" * 84)
(root / "steps/alpha.py").write_text(BASE.replace('"weight":1.0', '"weight":1.0  # edited, same behaviour'))
from qanat.project import load
p2, r2 = load(root)
c = backtest(store, p2, r2, "2024-02-01", "2024-05-01", seed=7)
print(f"  digest before edit {a.digest}")
print(f"  digest after  edit {c.digest}")
print(f"  -> {'digest changed (correct: the script moved)' if c.digest!=a.digest else 'DIGEST DID NOT CHANGE'}")

print()
print("=" * 84)
print("H11c  change an OPTION that the alpha reads. Does the digest notice?")
print("=" * 84)
y = (root / "qanat.yaml").read_text()
y = y.replace("    universe: u", "    universe: u\n    options: {lookback: 99}")
(root / "qanat.yaml").write_text(y)
p3, r3 = load(root)
d = backtest(store, p3, r3, "2024-02-01", "2024-05-01", seed=7)
print(f"  digest with lookback 99: {d.digest}   (was {c.digest})")
print(f"  -> {'changed (correct)' if d.digest != c.digest else 'DID NOT CHANGE'}")

print()
print("=" * 84)
print("H11d  an alpha that uses random.choice -- does `seed` make it repeatable?")
print("=" * 84)
root2 = build("rand", bars, RANDOM, rebalance="5d")
p4, r4, store2, rep2, res2 = run_pipeline(root2)
runs = []
for s in [7, 7, 8]:
    x = backtest(store2, p4, r4, "2024-02-01", "2024-05-01", seed=s)
    runs.append((s, x.digest, x.totals["net"]))
for s, dg, net in runs:
    print(f"  seed {s}: digest {dg}  net {net:.10f}")
same = abs(runs[0][2] - runs[1][2]) < 1e-12
print(f"  -> seed 7 twice: {'repeatable' if same else 'NOT REPEATABLE'}")
print(f"  -> seed 8 differs from 7: {'yes' if abs(runs[2][2]-runs[0][2])>1e-12 else 'no (seed has no effect)'}")

print()
print("=" * 84)
print("H11e  change the DATA underneath, keep everything else. Digest?")
print("=" * 84)
bars2 = per_symbol_drift(CAL, {"A": 0.004, "B": 0.0005})   # A now drifts twice as fast
bars2.to_csv(root2 / "seed/bars.csv", index=False)
from qanat.runner import run_all
run_all(store2, p4, r4)
e = backtest(store2, p4, r4, "2024-02-01", "2024-05-01", seed=7)
print(f"  digest before data change: {runs[0][1]}  net {runs[0][2]:.6f}")
print(f"  digest after  data change: {e.digest}  net {e.totals['net']:.6f}")
print(f"  -> {'SAME DIGEST, DIFFERENT ANSWER' if e.digest==runs[0][1] else 'digest changed'}")
store.close(); store2.close()
