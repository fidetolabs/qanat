import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import numpy as np, pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
bars = per_symbol_drift(CAL, {"A": 0.002, "B": 0.0005})

FLIP = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max()); mx = df["date"].max()
    if d.toordinal() % 2 == 0:
        w = [{"symbol":"A","weight": 0.5,"as_of":mx},{"symbol":"B","weight":-0.5,"as_of":mx}]
    else:
        w = [{"symbol":"A","weight":-0.5,"as_of":mx},{"symbol":"B","weight": 0.5,"as_of":mx}]
    return pd.DataFrame(w)
'''
print("=" * 86)
print("H7b  DECAY end to end -- a long/short book that flips every rebalance")
print("=" * 86)
for dec in [0, 2, 3]:
    root = build(f"dec{dec}", bars, FLIP, rebalance="1d")
    p, r, store, rep, res = run_pipeline(root)
    bt = backtest(store, p, r, "2024-02-01", "2024-06-01", decay=(dec or None))
    w = store.bt_weights(bt.run_id)
    df = pd.DataFrame(w)
    # what the run RECORDED as held (pre-decay) vs what it PRICED (post-decay)
    exposure = df.groupby("as_of")["weight"].apply(lambda s: s.abs().sum()).round(3)
    print(f"  decay={dec}:  net {bt.totals['net']:>9.2%} · avg turnover/period "
          f"{np.mean([x.turnover for x in bt.periods]):.3f} · recorded |weights| {sorted(set(exposure))[:3]}")
    store.close()
print("  decay is documented as: 'the signal survives, the turnover falls'.")
print("  Turnover does fall. The book size falls with it, and nothing reports that.")

print()
print("=" * 86)
print("H8  a rebalance gap FINER than the data (daily grid, weekly prices)")
print("=" * 86)
WEEK = pd.date_range("2024-01-01", periods=40, freq="7D")
wb = per_symbol_drift(WEEK, {"A": 0.01, "B": 0.01})
HOLD = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''
root = build("fine", wb, HOLD, rebalance="1d")
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-01-08", "2024-06-01")
print(f"  stops asked for: 145 (daily) · prices available: weekly")
print(f"  periods scored : {len(bt.periods)} · notes: {len(bt.notes)}")
print(f"  net            : {bt.totals['net']:.4%}")
skipped = [n for n in bt.notes if "same day" in n]
print(f"  'entry and exit on the same day, skipped' notes: {len(skipped)}")
print(f"  -> {len(bt.periods)} real periods out of 145 asked; is that visible in the headline? "
      f"periods field says {bt.totals['periods']}")
store.close()

print()
print("=" * 86)
print("H9  an alpha that returns duplicate symbols, and one that returns |w| != 1")
print("=" * 86)
DUP = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    mx = df["date"].max()
    return pd.DataFrame([{"symbol":"A","weight":0.6,"as_of":mx},
                         {"symbol":"A","weight":0.9,"as_of":mx},
                         {"symbol":"B","weight":0.4,"as_of":mx}])
'''
root = build("dup", bars, DUP, rebalance="5d")
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-02-01", "2024-05-01")
w = pd.DataFrame(store.bt_weights(bt.run_id))
first = w[w["as_of"] == w["as_of"].min()]
print(f"  alpha wrote A twice (0.6 and 0.9) and B once (0.4); |w| = 1.9")
print(f"  what the replay priced: {first[['symbol','weight']].to_dict('records')}")
print(f"  |weights| actually used: {first['weight'].abs().sum():.2f}")
evs = [e["message"] for e in store.recent_events(300) if "weights" in e["message"] and "sum" in e["message"]]
print(f"  warning raised? {evs[0][:70] if evs else 'NO WARNING'}")
print(f"  net reported: {bt.totals['net']:.2%}  (on a book 1.5x the declared size)")
store.close()
