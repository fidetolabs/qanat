"""H3: the leakage battery. Six ways an alpha might reach the future.

The data: every day exactly one of A/B jumps +2% and the other -2%, in a seeded
pattern with no signal in the past. A blind alpha earns ~0. An alpha that can see
two days ahead earns ~+2% per period, which compounds to something absurd.
So the net IS the leak detector.
"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import numpy as np, pandas as pd
from harness import build, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
rng = np.random.default_rng(11)
winner = rng.integers(0, 2, len(CAL))          # 0 -> A wins that day, 1 -> B wins
rows, pa, pb = [], 100.0, 100.0
for i, d in enumerate(CAL):
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "A", "close": round(pa, 10)})
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "B", "close": round(pb, 10)})
    up, dn = 1.02, 0.98
    pa *= up if winner[i] == 0 else dn
    pb *= dn if winner[i] == 0 else up
BARS = pd.DataFrame(rows)

PICK = '''
    # the two days after the newest date this alpha could see
    piv = full.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    piv.index = pd.to_datetime(piv.index)
    seen = pd.Timestamp(visible_max)
    later = piv.index[piv.index > seen]
    if len(later) < 2:
        pick = "A"
    else:
        d1, d2 = later[0], later[1]
        pick = "A" if piv.loc[d2, "A"] / piv.loc[d1, "A"] > piv.loc[d2, "B"] / piv.loc[d1, "B"] else "B"
    return pd.DataFrame([{"symbol": pick, "weight": 1.0, "as_of": visible_max}])
'''

def alpha(getter):
    return ('import pandas as pd\n'
            'def run(ctx):\n'
            '    legit = ctx.read("normalized.prices")\n'
            '    if legit.empty:\n'
            '        return pd.DataFrame(columns=["symbol","weight","as_of"])\n'
            '    visible_max = legit["date"].max()\n'
            f'    {getter}\n'
            '    if full is None or len(full) == 0:\n'
            '        return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":visible_max}])\n'
            + PICK)

VECTORS = {
 "V0 honest baseline (hold A)":
    'full = None',
 "V1 ctx.read() -- the sanctioned path":
    'full = legit',
 "V2 ctx.sql() unqualified table name":
    'full = ctx.sql("SELECT date, symbol, close FROM normalized__prices")',
 "V3 ctx.sql() schema-qualified (main.)":
    'full = ctx.sql("SELECT date, symbol, close FROM main.normalized__prices")',
 "V4 ctx.sql() against the raw table":
    'full = ctx.sql("SELECT date, symbol, close FROM main.raw__bars")',
 "V5 ctx.store.read() -- skips ctx.read":
    'full = ctx.store.read("normalized.prices")',
 "V6 read the source CSV off disk":
    'full = pd.read_csv(ctx.root / "seed/bars.csv")',
}

print("=" * 88)
print("H3  LEAKAGE BATTERY -- a blind alpha earns ~0%; a leaking one earns thousands of %")
print("=" * 88)
for i, (label, getter) in enumerate(VECTORS.items()):
    try:
        root = build(f"leak{i}", BARS, alpha(getter), rebalance="1d")
        p, r, store, rep, res = run_pipeline(root)
        bad = [x for x in res if not x.ok]
        if bad:
            print(f"  {label:44} pipeline failed: {bad[0].error[:60]}")
            store.close(); continue
        bt = backtest(store, p, r, "2024-01-20", "2024-06-01")
        net = bt.totals.get("net")
        fails = len(bt.failures)
        verdict = "LEAK" if (net is not None and net > 1.0) else ("blocked" if net is not None and abs(net) < 1.0 else "?")
        print(f"  {label:44} net {net:>14.2%}  periods {len(bt.periods):>4}  fails {fails:>3}  -> {verdict}")
        store.close()
    except Exception as exc:
        print(f"  {label:44} raised {type(exc).__name__}: {str(exc)[:70]}")
