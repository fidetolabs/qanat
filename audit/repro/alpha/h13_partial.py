"""H13: an alpha that crashes on the bad days. What does the headline say?

This is the realistic shape of a research bug: a divide-by-zero or a KeyError that
only triggers on certain data. If those days are the losing days, the surviving
periods are biased upward, and the net is a number about a subset nobody chose.
"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import numpy as np, pandas as pd
from harness import build, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
rng = np.random.default_rng(3)
px, rows = 100.0, []
rets = rng.normal(0.0005, 0.02, len(CAL))
for i, d in enumerate(CAL):
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "A", "close": round(px, 10)})
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "B", "close": round(px*0.9, 10)})
    px *= (1 + rets[i])
BARS = pd.DataFrame(rows)

HEALTHY = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
# the same alpha, but it throws whenever the last 3 days were down
FLAKY = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    a = df[df.symbol=="A"].sort_values("date")["close"]
    if len(a) > 4 and a.iloc[-1] < a.iloc[-4]:
        raise ZeroDivisionError("division by zero")     # only bites on down days
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
print("=" * 88)
print("H13  the same alpha, healthy vs crashing on down days")
print("=" * 88)
out = {}
for name, src in [("healthy", HEALTHY), ("flaky", FLAKY)]:
    root = build(f"pt_{name}", BARS, src, rebalance="1d")
    p, r, store, rep, res = run_pipeline(root)
    bt = backtest(store, p, r, "2024-01-15", "2024-06-15")
    row = store.backtest(bt.run_id)
    out[name] = bt
    print(f"  {name:8} status={row['status']:<8} periods={len(bt.periods):>4} "
          f"failures={len(bt.failures):>4}  net={bt.totals['net']:>9.2%}")
    store.close()

h, f = out["healthy"], out["flaky"]
print()
print(f"  the bug moved the answer by {f.totals['net'] - h.totals['net']:+.2%}")
print(f"  the flaky run priced {len(f.periods)} of the {len(h.periods)} periods the healthy one did")
print()
print("  what the flaky report tells a reader:")
print(f"    status            : partial")
print(f"    totals['periods'] : {f.totals['periods']}")
print(f"    notes             : {len(f.notes)}")
if f.notes:
    print(f"      -> {f.notes[0][:100]}")
print(f"    failures listed   : {len(f.failures)}  (first: {f.failures[0][:70] if f.failures else '-'})")
print(f"    net presented as  : {f.totals['net']:.2%}   <- no marker on the number itself")
print()
print("  and in the strategy book:")
import json
for row in [store.backtest(f.run_id)] if False else []:
    pass
