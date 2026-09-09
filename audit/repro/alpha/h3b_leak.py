"""H3b: where exactly is the boundary? Same data, more precise vectors."""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import numpy as np, pandas as pd
from harness import build, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
rng = np.random.default_rng(11)
winner = rng.integers(0, 2, len(CAL))
rows, pa, pb = [], 100.0, 100.0
for i, d in enumerate(CAL):
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "A", "close": round(pa, 10)})
    rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": "B", "close": round(pb, 10)})
    pa *= 1.02 if winner[i] == 0 else 0.98
    pb *= 0.98 if winner[i] == 0 else 1.02
BARS = pd.DataFrame(rows)

PICK = '''
    piv = full.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    piv.index = pd.to_datetime(piv.index)
    later = piv.index[piv.index > pd.Timestamp(visible_max)]
    if len(later) < 2:
        pick = "A"
    else:
        d1, d2 = later[0], later[1]
        pick = "A" if piv.loc[d2,"A"]/piv.loc[d1,"A"] > piv.loc[d2,"B"]/piv.loc[d1,"B"] else "B"
    return pd.DataFrame([{"symbol": pick, "weight": 1.0, "as_of": visible_max}])
'''
def alpha(getter, reads_raw=False):
    return ('import pandas as pd\n'
            'def run(ctx):\n'
            '    legit = ctx.read("normalized.prices")\n'
            '    if legit.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])\n'
            '    visible_max = legit["date"].max()\n'
            f'    {getter}\n'
            '    if full is None or len(full)==0:\n'
            '        return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":visible_max}])\n' + PICK)

V = {
 "baseline (no cheat)":                      ('full = None', False),
 "sql  raw__bars           (unqualified)":   ('full = ctx.sql("SELECT date,symbol,close FROM raw__bars")', False),
 "sql  main.raw__bars      (qualified)":     ('full = ctx.sql("SELECT date,symbol,close FROM main.raw__bars")', False),
 "sql  qanat_pit.raw__bars (the view)":      ('full = ctx.sql("SELECT date,symbol,close FROM qanat_pit.raw__bars")', False),
 "ctx.store.read('raw.bars')":               ('full = ctx.store.read("raw.bars")', False),
 "ctx.read('raw.bars') + declared in from:": ('full = ctx.read("raw.bars")', True),
 "pd.read_csv(ctx.root/seed)":               ('full = pd.read_csv(ctx.root / "seed/bars.csv")', False),
}

print("=" * 92)
print("H3b  exactly which door is open?   net > 100% means the alpha saw the future")
print("=" * 92)
for i, (label, (getter, reads_raw)) in enumerate(V.items()):
    extra = ""
    src = alpha(getter)
    if reads_raw:
        src = src.replace('ctx.read("normalized.prices")', 'ctx.read("normalized.prices")')
    try:
        root = build(f"lk{i}", BARS, src, rebalance="1d")
        if reads_raw:   # declare raw.bars on the alpha step so ctx.read allows it
            y = (root / "qanat.yaml").read_text().replace(
                "    from: [normalized.prices]\n    to: [weights.h]",
                "    from: [normalized.prices, raw.bars]\n    to: [weights.h]")
            (root / "qanat.yaml").write_text(y)
        p, r, store, rep, res = run_pipeline(root)
        bad = [x for x in res if not x.ok]
        if bad:
            print(f"  {label:42} PIPELINE FAILED: {bad[0].error[:44]}"); store.close(); continue
        bt = backtest(store, p, r, "2024-01-20", "2024-06-01")
        net = bt.totals.get("net"); fails = len(bt.failures)
        tag = "*** LEAK ***" if net and net > 1.0 else "blocked"
        print(f"  {label:42} net {net:>12.2%}  fails {fails:>3}  {tag}")
        store.close()
    except Exception as exc:
        print(f"  {label:42} raised {type(exc).__name__}: {str(exc)[:58]}")
