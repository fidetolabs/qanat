"""H5: a symbol that dies mid-window. Does the loss get counted?

Hypothesis under test: 'equal-weight these 4 names'. One of them (D) goes to zero
on day 100 -- a delisting. An honest backtest must show that -25% hit. The whole
sales pitch of a point-in-time engine is that it does.
"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
DEATH = 100

def make(mode):
    rows = []
    for s in ["A", "B", "C", "D"]:
        px = 100.0
        for i, d in enumerate(CAL):
            if s == "D" and i >= DEATH:
                if mode == "vanish":          # rows simply stop -- what a real feed does
                    break
                if mode == "zero":            # the price is printed as 0
                    px = 0.0
                if mode == "last":            # price frozen at its last value
                    pass
            rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "close": round(px, 10)})
            if not (s == "D" and i >= DEATH):
                px *= 1.0005
    return pd.DataFrame(rows)

EQUAL = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    syms = sorted(ctx.universe()["symbol"])
    return pd.DataFrame([{"symbol": s, "weight": 1.0/len(syms), "as_of": df["date"].max()}
                         for s in syms])
'''

print("=" * 90)
print("H5  one of four equally-weighted names dies on day 100")
print("=" * 90)
print("  An honest engine must show the loss. 'A,B,C only' is the no-D control.\n")
for mode in ["vanish", "zero", "last"]:
    root = build(f"surv_{mode}", make(mode), EQUAL, rebalance="5d")
    p, r, store, rep, res = run_pipeline(root)
    bt = backtest(store, p, r, "2024-01-10", "2024-07-15")
    notes_about_d = [n for n in bt.notes if "D" in n]
    print(f"  D's prices {mode:7} -> net {bt.totals['net']:>9.2%} · periods {len(bt.periods):>3} "
          f"· notes mentioning D: {len(notes_about_d)}")
    if notes_about_d:
        print(f"      first note: {notes_about_d[0][:88]}")
    store.close()

# the control: same alpha, D never existed
bars3 = make("vanish")
bars3 = bars3[bars3["symbol"] != "D"]
root = build("surv_none", bars3, EQUAL, rebalance="5d", symbols=["A","B","C"])
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-01-10", "2024-07-15")
print(f"\n  CONTROL: A,B,C only (D never existed) -> net {bt.totals['net']:>9.2%}")
print("  If 'vanish' matches the control, the dead name cost the portfolio nothing.")
store.close()
