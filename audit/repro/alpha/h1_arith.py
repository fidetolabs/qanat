"""H1/H2: does the reported net match arithmetic I can do by hand?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import build, run_pipeline, backtest, per_symbol_drift

CAL = pd.date_range("2024-01-01", periods=200, freq="D")   # every calendar day priced

HOLD_A = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
'''

ALTERNATE = '''
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max())
    pick = "A" if (d.toordinal() % 2 == 0) else "B"
    return pd.DataFrame([{"symbol":pick,"weight":1.0,"as_of":df["date"].max()}])
'''

G = 0.001   # every symbol compounds at exactly +0.1% per calendar day
bars = per_symbol_drift(CAL, {"A": G, "B": G})

print("=" * 74)
print("H1  hold A always · +0.1%/day · no costs · rebalance 1d")
print("=" * 74)
root = build("h1", bars, HOLD_A, rebalance="1d", fee=0.0, slip=0.0)
p, r, store, rep, res = run_pipeline(root)
print("  check:", "ok" if rep.ok else rep.errors)
print("  pipeline:", [(x.job_id, x.status, x.rows) for x in res])
bt = backtest(store, p, r, "2024-01-10", "2024-06-01")
n = len(bt.periods)
expected = (1 + G) ** n - 1
got = bt.totals["net"]
print(f"  periods:            {n}")
print(f"  expected net:       {expected:.10f}   ((1+0.001)^{n} - 1)")
print(f"  qanat net:          {got:.10f}")
print(f"  difference:         {abs(got-expected):.2e}   -> {'MATCH' if abs(got-expected)<1e-9 else 'MISMATCH'}")
print(f"  gross per period:   {bt.periods[0].gross:.10f}  (expected {G})")
print(f"  turnover, period 1: {bt.periods[0].turnover}  (expected 1.0, buying A from flat)")
print(f"  turnover, period 2: {bt.periods[1].turnover}  (expected 0.0, nothing changed)")
print(f"  notes: {bt.notes[:2]}")
store.close()

print()
print("=" * 74)
print("H2  alternate A/B every day · fee 10bps + slip 5bps · turnover must be 2.0")
print("=" * 74)
root = build("h2", bars, ALTERNATE, rebalance="1d", fee=10.0, slip=5.0)
p, r, store, rep, res = run_pipeline(root)
bt = backtest(store, p, r, "2024-01-10", "2024-06-01")
mid = bt.periods[3]
print(f"  periods:          {len(bt.periods)}")
print(f"  turnover:         {mid.turnover}      (expected 2.0: sell one, buy the other)")
print(f"  fees:             {mid.fees:.8f}  (expected {2.0*10/10000:.8f})")
print(f"  slippage:         {mid.slippage:.8f}  (expected {2.0*5/10000:.8f})")
print(f"  gross:            {mid.gross:.8f}  (expected {G})")
print(f"  net:              {mid.net:.8f}  (expected {G - 2.0*15/10000:.8f})")
ok = (abs(mid.turnover-2.0)<1e-9 and abs(mid.fees-0.002)<1e-9 and abs(mid.net-(G-0.003))<1e-9)
print(f"  -> {'MATCH' if ok else 'MISMATCH'}")
print()
print("  totals reconcile?")
t = bt.totals
print(f"    net_sum        {t['net_sum']:.8f}")
print(f"    gross-fees-slp {t['gross']-t['fees']-t['slippage']:.8f}")
print(f"    -> {'consistent' if abs(t['net_sum']-(t['gross']-t['fees']-t['slippage']))<1e-9 else 'INCONSISTENT'}")
store.close()
