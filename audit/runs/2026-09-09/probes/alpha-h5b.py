"""H5b: what does the report SAY it held, when one holding has no price?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D"); DEATH = 100
rows = []
for s in ["A","B","C","D"]:
    px = 100.0
    for i, d in enumerate(CAL):
        if s == "D" and i >= DEATH: break          # the feed simply stops sending D
        rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "close": round(px,10)})
        px *= 1.0005
BARS = pd.DataFrame(rows)

EQUAL = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    syms = sorted(ctx.universe()["symbol"])
    return pd.DataFrame([{"symbol":s,"weight":1.0/len(syms),"as_of":df["date"].max()} for s in syms])
'''
root = build("h5b", BARS, EQUAL, rebalance="5d")
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-01-10", "2024-07-15")

dead_day = CAL[DEATH]
print("D stops being priced after", CAL[DEATH-1].date())
print(f"{'as_of':<12}{'holdings':>9}{'gross':>11}   what the report claims")
for per in bt.periods:
    a = pd.Timestamp(per.as_of)
    if CAL[DEATH-4] <= a <= CAL[DEATH+16]:
        flag = "  <- D has no price here" if a >= dead_day else ""
        print(f"{str(a.date()):<12}{per.holdings:>9}{per.gross:>11.6f}{flag}")
print()
print("total notes in the whole report:", len(bt.notes))
print("notes naming a missing price   :", len([n for n in bt.notes if 'no price' in n]))
print()
# what a human would check: did the money that was in D go anywhere?
last = bt.periods[-1]
print(f"last period: holdings={last.holdings}, gross={last.gross:.6f}")
print(f"A,B,C each grew 0.05%/day, so 3 held names at 1/4 each should give ~{3*0.25*(1.0005**5-1):.6f}")
print(f"four live names would give                                        ~{4*0.25*(1.0005**5-1):.6f}")
print()
print("=> the D sleeve earns exactly 0 and is reported as a holding.")
store.close()
