"""H4/H7: do purge, embargo and decay do what the docs say?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift

CAL = pd.date_range("2024-01-01", periods=200, freq="D")

# ---- purge: the alpha reports the newest date it can see -------------------
SEE = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    ctx.log("newest visible date: " + str(pd.Timestamp(df["date"].max()).date()))
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
bars = per_symbol_drift(CAL, {"A": 0.001, "B": 0.001})
print("=" * 84)
print("H4a  PURGE -- 'hold rows back this long before a step may read them'")
print("=" * 84)
for purge in ["0d", "5d", "20d"]:
    root = build(f"pg{purge}", bars, SEE, rebalance="10d", purge=purge)
    p, r, store, rep, res = run_pipeline(root)
    bt = backtest(store, p, r, "2024-03-01", "2024-04-01")
    evs = [e["message"] for e in store.recent_events(400) if "newest visible" in e["message"]]
    seen = sorted({e.split(": ")[1] for e in evs})
    print(f"  purge={purge:<4} stops are 03-01,03-11,03-21,03-31 · dates the alpha could see: {seen[-4:]}")
    store.close()
print("  expected: purge d shifts what the alpha sees back by exactly d days")

# ---- embargo: which prices the period is filled at -------------------------
print()
print("=" * 84)
print("H4b  EMBARGO -- 'wait this long after as_of before a return counts'")
print("=" * 84)
HOLD_A = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
for emb in ["0d", "3d"]:
    root = build(f"eb{emb}", bars, HOLD_A, rebalance="10d", embargo=emb)
    p, r, store, rep, res = run_pipeline(root)
    bt = backtest(store, p, r, "2024-03-01", "2024-04-01")
    per = bt.periods[0]
    print(f"  embargo={emb:<4} as_of {str(pd.Timestamp(per.as_of).date())} "
          f"-> filled {str(pd.Timestamp(per.priced_from).date())} .. {str(pd.Timestamp(per.priced_to).date())}")
    store.close()
print("  expected: embargo 3d moves BOTH ends 3 days later (as_of+3 -> next+3)")

# ---- decay: does blending keep the book the same size? ---------------------
print()
print("=" * 84)
print("H7  DECAY on a LONG-SHORT book -- 'the signal survives, the turnover falls'")
print("=" * 84)
from qanat.backtest import decay_weights
stops = ["s1","s2","s3","s4"]
held = {"s1": pd.Series({"A": 0.5, "B": -0.5}),
        "s2": pd.Series({"A": -0.5, "B": 0.5}),
        "s3": pd.Series({"A": 0.5, "B": -0.5}),
        "s4": pd.Series({"A": -0.5, "B": 0.5})}
print("  a book that flips sign each rebalance, |weights| = 1.00 every time")
for n in [0, 2, 3, 4]:
    out = decay_weights(held, stops, n) if n > 1 else held
    sizes = [round(float(out[s].abs().sum()), 4) for s in stops]
    print(f"    decay={n}:  |weights| per stop -> {sizes}")
print()
print("  and on a LONG-ONLY book that rotates between two disjoint names:")
held2 = {"s1": pd.Series({"A": 1.0}), "s2": pd.Series({"B": 1.0}),
         "s3": pd.Series({"A": 1.0}), "s4": pd.Series({"B": 1.0})}
for n in [0, 2, 3]:
    out = decay_weights(held2, stops, n) if n > 1 else held2
    sizes = [round(float(out[s].abs().sum()), 4) for s in stops]
    print(f"    decay={n}:  |weights| per stop -> {sizes}")
