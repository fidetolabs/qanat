"""H11e redo: does the digest move when the DATA changes underneath?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift
from qanat.runner import run_all
from qanat.project import load

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
HOLD_A = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
root = build("digest", per_symbol_drift(CAL, {"A": 0.002, "B": 0.001}), HOLD_A, rebalance="5d")
p, r, store, rep, res = run_pipeline(root)
a = backtest(store, p, r, "2024-02-01", "2024-05-01", seed=7)
px1 = store.read("normalized.prices")
print(f"  run 1  digest {a.digest}  net {a.totals['net']:.6f}  "
      f"A last close {px1[px1.symbol=='A']['close'].iloc[-1]:.2f}")

# same file path, same project, different numbers inside the file
per_symbol_drift(CAL, {"A": 0.006, "B": 0.001}).to_csv(root / "seed/bars.csv", index=False)
p, r = load(root)
out = run_all(store, p, r)
print(f"  re-polled the source: {[(x.job_id, x.status, x.rows) for x in out]}")
px2 = store.read("normalized.prices")
print(f"  A last close is now {px2[px2.symbol=='A']['close'].iloc[-1]:.2f}  (was {px1[px1.symbol=='A']['close'].iloc[-1]:.2f})")

b = backtest(store, p, r, "2024-02-01", "2024-05-01", seed=7)
print(f"  run 2  digest {b.digest}  net {b.totals['net']:.6f}")
print()
print(f"  same digest?  {a.digest == b.digest}")
print(f"  same answer?  {abs(a.totals['net']-b.totals['net']) < 1e-9}")
print()
from qanat.backtest import compare
c = compare(store.backtest(a.run_id), store.backtest(b.run_id))
print(f"  what `qanat compare` says: same_question={c['same_question']}")
print(f"    \"{c['note']}\"")
print(f"    net moved {c['moved']['net']['a']:.6f} -> {c['moved']['net']['b']:.6f}")
store.close()
