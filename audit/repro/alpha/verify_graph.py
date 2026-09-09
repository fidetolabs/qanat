"""Verify the four strongest graph claims myself."""
import sys, shutil; sys.path.insert(0, "/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")
import pandas as pd
from harness import BASE, per_symbol_drift, NORMALIZE
from qanat.project import load, validate
from qanat.runner import run_all
from qanat.store import Store

CAL = pd.date_range("2024-01-01", periods=60, freq="D")
def scaffold(name, yaml_body, steps: dict):
    root = BASE / name
    if root.exists(): shutil.rmtree(root)
    (root/"steps").mkdir(parents=True); (root/"seed").mkdir(); (root/"universes").mkdir()
    per_symbol_drift(CAL, {"A": 0.001}).to_csv(root/"seed/bars.csv", index=False)
    pd.DataFrame({"symbol": ["A"]}).to_csv(root/"universes/u.csv", index=False)
    (root/"steps/normalize.sql").write_text(NORMALIZE)
    for fn, body in steps.items(): (root/"steps"/fn).write_text(body)
    (root/"qanat.yaml").write_text(yaml_body)
    return root

HEAD = """project: v
store: ./data/q.duckdb
universes:
  - {id: u, index: T, symbols: ./universes/u.csv}
stages:
  - {id: raw, kind: raw}
  - {id: normalized, kind: features}
  - {id: weights, kind: weights}
  - {id: pnl, kind: pnl}
sources:
  - {id: bars, to: [raw.bars], connector: csv, mode: replace, options: {path: ./seed/bars.csv}}
steps:
  - {id: normalize, from: [raw.bars], to: [normalized.prices], script: steps/normalize.sql}
"""
ALPHA = """  - id: alpha_h
    from: [normalized.prices]
    to: [weights.h]
    script: steps/alpha.py
    universe: u
"""
TAIL = """backtest:
  prices: normalized.prices
  price_column: close
  symbol_column: symbol
  date_column: date
  rebalance: "5d"
"""
A_SRC = ('import pandas as pd\ndef run(ctx):\n'
         '    df = ctx.read("normalized.prices")\n'
         '    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])\n'
         '    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])\n')

print("=" * 84)
print("CLAIM 1  a cycle on a POPULATED store: values grow every run, all 'ok'")
print("=" * 84)
root = scaffold("v_cyc", HEAD +
  "  - {id: feat_a, from: [normalized.prices], to: [normalized.a], script: steps/fa.sql}\n"
  "  - {id: feat_b, from: [normalized.a], to: [normalized.b], script: steps/fb.sql}\n" + ALPHA + TAIL,
  {"fa.sql": "SELECT date, symbol, close*7 AS v FROM normalized__prices",
   "fb.sql": "SELECT date, symbol, v*1.0 AS v FROM normalized__a", "alpha.py": A_SRC})
p, r = load(root); s = Store(p.store_url(r)); run_all(s, p, r)
print("  clean pipeline, sum of normalized.a =", round(float(s.read("normalized.a")["v"].sum()), 2))
y = (root/"qanat.yaml").read_text().replace(
    "{id: feat_a, from: [normalized.prices], to: [normalized.a], script: steps/fa.sql}",
    "{id: feat_a, from: [normalized.b], to: [normalized.a], script: steps/fa.sql}")
(root/"qanat.yaml").write_text(y)
(root/"steps/fa.sql").write_text("SELECT date, symbol, v*10 AS v FROM normalized__b")
p, r = load(root); rep = validate(p, r)
print("  now feat_a reads b and feat_b reads a -- a cycle.")
print("  qanat check errors:", rep.errors or "NONE")
for i in range(3):
    res = run_all(s, p, r)
    bad = [x.job_id for x in res if not x.ok]
    print(f"  run {i+1}: all ok? {not bad}  sum(normalized.a) = {float(s.read('normalized.a')['v'].sum()):,.0f}")
s.close()

print()
print("=" * 84)
print("CLAIM 2  two alpha steps writing ONE weights table = two alphas in the book")
print("=" * 84)
root = scaffold("v_dup", HEAD +
  "  - {id: alpha_g, from: [normalized.prices], to: [weights.h], script: steps/alpha.py, universe: u}\n"
  + ALPHA + TAIL, {"alpha.py": A_SRC})
p, r = load(root); rep = validate(p, r); s = Store(p.store_url(r)); run_all(s, p, r)
print("  qanat check errors:", rep.errors or "NONE")
print("  project.alphas ->", p.alphas)
from qanat.backtest import run_backtest
for a in ["alpha_g", "alpha_h"]:
    run_backtest(s, p, r, "2024-01-10", "2024-02-20", alpha=a)
print("  strategy book:")
for row in s.alpha_book():
    print(f"    {row['alpha']:<10} last_net {row['last_net']:.12f}")
print("  -> the same portfolio, counted as two alphas" )
s.close()

print()
print("=" * 84)
print("CLAIM 3  a downstream step runs after its upstream FAILED, and reports ok")
print("=" * 84)
root = scaffold("v_fail", HEAD +
  "  - {id: feat_a, from: [normalized.prices], to: [normalized.a], script: steps/fa.py}\n"
  "  - {id: feat_b, from: [normalized.a], to: [normalized.b], script: steps/fb.sql}\n" + ALPHA + TAIL,
  {"fa.py": ('import pandas as pd\ndef run(ctx):\n'
             '    df = ctx.read("normalized.prices")\n'
             '    return pd.DataFrame({"date": df["date"], "symbol": df["symbol"], "v": df["close"]*7})\n'),
   "fb.sql": "SELECT date, symbol, v FROM normalized__a", "alpha.py": A_SRC})
p, r = load(root); s = Store(p.store_url(r)); run_all(s, p, r)
before = float(s.read("normalized.b")["v"].sum())
print(f"  healthy run: sum(normalized.b) = {before:,.2f}")
(root/"steps/fa.py").write_text('def run(ctx):\n    raise RuntimeError("the vendor file was truncated")\n')
p, r = load(root)
res = run_all(s, p, r)
print("  after feat_a starts failing:")
for x in res:
    if x.job_id in ("feat_a", "feat_b"):
        print(f"    {x.job_id:<8} {x.status:<7} rows={x.rows}  {(x.error or '')[:44]}")
after = float(s.read("normalized.b")["v"].sum())
print(f"  sum(normalized.b) = {after:,.2f}   {'(unchanged: stale data, reported ok)' if after==before else ''}")
from qanat.plan import plan as mkplan
pl = mkplan(p, r, s)
print(f"  qanat plan: {len(pl.changes)} change(s), unchanged={pl.unchanged}")
s.close()

print()
print("=" * 84)
print("CLAIM 4  a misspelled option leaves ${var} in the SQL -> 0 rows, status ok")
print("=" * 84)
root = scaffold("v_opt", HEAD +
  "  - {id: filt, from: [normalized.prices], to: [normalized.filt], script: steps/f.sql, options: {other: A}}\n"
  + ALPHA + TAIL,
  {"f.sql": "SELECT * FROM normalized__prices WHERE symbol = '${sym}'", "alpha.py": A_SRC})
p, r = load(root); rep = validate(p, r); s = Store(p.store_url(r))
res = run_all(s, p, r)
f = [x for x in res if x.job_id == "filt"][0]
print("  qanat check errors:", rep.errors or "NONE")
print(f"  step 'filt' -> status={f.status}  rows={f.rows}")
print(f"  normalized.filt row count: {len(s.read('normalized.filt'))}")
print("  -> a YAML typo empties the table and nothing anywhere says so")
s.close()
