"""H10: when something is wrong with the book, does the REPORT say so?"""
import sys, json; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift
from qanat.backtest import period_detail

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
bars = per_symbol_drift(CAL, {"A": 0.002, "B": 0.0005})

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
root = build("rep", bars, DUP, rebalance="5d")
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-02-01", "2024-05-01")

print("=" * 84)
print("An over-levered, duplicated book (|w| = 1.9). Where does that fact appear?")
print("=" * 84)
print(f"  event log            : {'YES' if any('sum to' in e['message'] for e in store.recent_events(300)) else 'no'}")
print(f"  report .notes        : {'YES' if any('sum to' in n or 'weight' in n for n in bt.notes) else 'NO'}   (notes: {len(bt.notes)})")
print(f"  report .conditions   : {'YES' if any('weight' in str(k)+str(v) for k,v in bt.conditions.items()) else 'NO'}")
print(f"  report .failures     : {'YES' if bt.failures else 'NO'}")
print(f"  run status           : {store.backtest(bt.run_id)['status']}")
print(f"  headline net         : {bt.totals['net']:.2%}")

print()
print("What the drill-down says was held at one rebalance:")
d = period_detail(store, p, bt.run_id, str(pd.Timestamp(bt.periods[2].as_of)))
for h in d["holdings"]:
    print(f"    {h['symbol']}  weight {h['weight']:<6} traded {h['traded']:<8} contribution {h['contribution']}")
print(f"    period gross reported here: {d['period']['gross']:.6f}")
print()
tot = sum(abs(h['weight']) for h in d['holdings'])
print(f"  drill-down |weights| : {tot:.2f}")
print(f"  priced   |weights|   : 1.30   (groupby(level=0).last() keeps A=0.9, drops A=0.6)")
print(f"  alpha wrote          : 1.90")
print("  -> three different book sizes for one rebalance, none of them 1.0")
store.close()

print()
print("=" * 84)
print("DECAY: does the saved 'what was held' match what was actually priced?")
print("=" * 84)
FLIP = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max()); mx = df["date"].max()
    s = 1.0 if d.toordinal() % 2 == 0 else -1.0
    return pd.DataFrame([{"symbol":"A","weight":0.5*s,"as_of":mx},
                         {"symbol":"B","weight":-0.5*s,"as_of":mx}])
'''
root = build("dec_rep", bars, FLIP, rebalance="1d")
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-02-01", "2024-04-01", decay=3)
w = pd.DataFrame(store.bt_weights(bt.run_id))
exp = w.groupby("as_of")["weight"].apply(lambda s: s.abs().sum())
print(f"  decay = 3")
print(f"  saved in _qanat_bt_weights : |weights| = {sorted(set(exp.round(3)))}   <- the raw alpha output")
print(f"  average turnover per period: {sum(x.turnover for x in bt.periods)/len(bt.periods):.3f}")
print(f"  turnover with decay off would be ~2.0 for a book that flips a 1.0-sized bet")
print(f"  -> turnover {sum(x.turnover for x in bt.periods)/len(bt.periods):.3f} implies the PRICED book was ~"
      f"{sum(x.turnover for x in bt.periods)/len(bt.periods)/2:.2f}, not the 1.0 that was saved")
store.close()
