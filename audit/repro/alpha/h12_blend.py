"""H12: two hypotheses priced as one book. Is the allocation arithmetic right?"""
import sys; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import shutil, pandas as pd
from pathlib import Path
from harness import BASE, per_symbol_drift, NORMALIZE, run_pipeline, backtest

CAL = pd.date_range("2024-01-01", periods=200, freq="D")
GA, GB = 0.002, 0.0005
bars = per_symbol_drift(CAL, {"A": GA, "B": GB})

def one(sym):
    return ('import pandas as pd\ndef run(ctx):\n'
            '    df = ctx.read("normalized.prices")\n'
            '    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])\n'
            f'    return pd.DataFrame([{{"symbol":"{sym}","weight":1.0,"as_of":df["date"].max()}}])\n')

root = BASE / "blend"
if root.exists(): shutil.rmtree(root)
(root/"steps").mkdir(parents=True); (root/"seed").mkdir(); (root/"universes").mkdir()
bars.to_csv(root/"seed/bars.csv", index=False)
pd.DataFrame({"symbol":["A","B"]}).to_csv(root/"universes/u.csv", index=False)
(root/"steps/normalize.sql").write_text(NORMALIZE)
(root/"steps/a_only.py").write_text(one("A"))
(root/"steps/b_only.py").write_text(one("B"))
(root/"qanat.yaml").write_text("""project: blend
store: ./data/q.duckdb
universes:
  - {id: u, index: TEST, symbols: ./universes/u.csv}
stages:
  - {id: raw, kind: raw}
  - {id: normalized, kind: features}
  - {id: weights, kind: weights}
  - {id: pnl, kind: pnl}
sources:
  - id: bars
    to: [raw.bars]
    connector: csv
    mode: replace
    options: {path: ./seed/bars.csv}
steps:
  - id: alpha_ay
    from: [normalized.prices]
    to: [weights.ay]
    script: steps/a_only.py
    universe: u
  - id: alpha_be
    from: [normalized.prices]
    to: [weights.be]
    script: steps/b_only.py
    universe: u
  - id: normalize
    from: [raw.bars]
    to: [normalized.prices]
    script: steps/normalize.sql
backtest:
  prices: normalized.prices
  price_column: close
  symbol_column: symbol
  date_column: date
  rebalance: "1d"
  fee_bps: 0.0
  slippage_bps: 0.0
""")
p, r, store, rep, res = run_pipeline(root)
print("  check:", "ok" if rep.ok else rep.errors)
print("  note: steps are declared out of dependency order on purpose (normalize is last)")
print("  pipeline:", [(x.job_id, x.status) for x in res])
print()
print("=" * 84)
print("H12  blending two hypotheses -- every number below is checkable by hand")
print("=" * 84)
solo_a = backtest(store, p, r, "2024-02-01", "2024-05-01", alpha="alpha_ay")
solo_b = backtest(store, p, r, "2024-02-01", "2024-05-01", alpha="alpha_be")
print(f"  A alone   gross/period {solo_a.periods[5].gross:.8f}   expected {GA:.8f}")
print(f"  B alone   gross/period {solo_b.periods[5].gross:.8f}   expected {GB:.8f}")

for alloc, label in [(None, "equal (default)"), ({"ay":3,"be":1}, "3:1 to A"), ({"ay":1,"be":0}, "all A")]:
    bt = backtest(store, p, r, "2024-02-01", "2024-05-01",
                  alpha=["alpha_ay","alpha_be"], allocation=alloc)
    sh = bt.conditions["allocation"]
    wa = sh.get("ay", 0.0); wb = sh.get("be", 0.0)
    exp = wa*GA + wb*GB
    got = bt.periods[5].gross
    print(f"  blend {label:<16} shares {sh}  gross {got:.8f}  expected {exp:.8f}  "
          f"{'MATCH' if abs(got-exp)<1e-10 else 'MISMATCH'}")

print()
print("  a zero allocation and a negative one:")
for alloc in [{"ay":0,"be":0}, {"ay":-1,"be":2}]:
    try:
        bt = backtest(store, p, r, "2024-02-01", "2024-05-01",
                      alpha=["alpha_ay","alpha_be"], allocation=alloc)
        print(f"    {alloc} -> accepted, net {bt.totals['net']:.4%}")
    except Exception as exc:
        print(f"    {alloc} -> refused: {str(exc)[:66]}")
print()
print("  asking for an alpha that is not in the project:")
try:
    backtest(store, p, r, "2024-02-01", "2024-05-01", alpha="alpha_nope")
except Exception as exc:
    print(f"    {str(exc)[:96]}")
store.close()
