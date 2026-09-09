"""Build a qanat project from scratch, with a CSV data source and a chosen alpha.

Everything here is the ordinary user path: write qanat.yaml, point a `csv` source
at a file, add steps, run the pipeline, backtest. No internal shortcuts.
"""
from __future__ import annotations
import shutil, textwrap
from pathlib import Path
import numpy as np, pandas as pd

BASE = Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/alab")

NORMALIZE = """SELECT CAST(date AS DATE) AS date, symbol,
       CAST(close AS DOUBLE) AS close
FROM raw__bars
QUALIFY row_number() OVER (PARTITION BY symbol, CAST(date AS DATE) ORDER BY date DESC) = 1
"""

def project_yaml(name, alpha_script="steps/alpha.py", rebalance="1d", fee=0.0, slip=0.0,
                 universe=True, extra_steps="", purge="0d", embargo="0d"):
    L = [f"project: {name}", "store: ./data/q.duckdb"]
    if universe:
        L += ["universes:", "  - {id: u, index: TEST, symbols: ./universes/u.csv}"]
    L += ["stages:",
          "  - {id: raw, kind: raw}",
          "  - {id: normalized, kind: features}",
          "  - {id: weights, kind: weights}",
          "  - {id: pnl, kind: pnl}",
          "sources:",
          "  - id: bars",
          "    to: [raw.bars]",
          "    connector: csv",
          "    mode: replace",
          "    options: {path: ./seed/bars.csv}",
          "steps:",
          "  - id: normalize",
          "    from: [raw.bars]",
          "    to: [normalized.prices]",
          "    script: steps/normalize.sql",
          "  - id: alpha_h",
          "    from: [normalized.prices]",
          "    to: [weights.h]",
          f"    script: {alpha_script}"]
    if universe:
        L += ["    universe: u"]
    if extra_steps:
        L += [x for x in extra_steps.rstrip("\n").split("\n")]
    L += ["backtest:",
          "  prices: normalized.prices",
          "  price_column: close",
          "  symbol_column: symbol",
          "  date_column: date",
          f'  rebalance: "{rebalance}"',
          f"  fee_bps: {fee}",
          f"  slippage_bps: {slip}",
          f'  purge: "{purge}"',
          f'  embargo: "{embargo}"']
    return "\n".join(L) + "\n"

def build(name: str, bars: pd.DataFrame, alpha_src: str, symbols=None, **kw) -> Path:
    root = BASE / name
    if root.exists():
        shutil.rmtree(root)
    (root / "steps").mkdir(parents=True)
    (root / "seed").mkdir()
    (root / "universes").mkdir()
    bars.to_csv(root / "seed/bars.csv", index=False)
    syms = symbols if symbols is not None else sorted(bars["symbol"].unique())
    pd.DataFrame({"symbol": syms}).to_csv(root / "universes/u.csv", index=False)
    (root / "steps/normalize.sql").write_text(NORMALIZE)
    (root / "steps/alpha.py").write_text(alpha_src)
    (root / "qanat.yaml").write_text(project_yaml(name, **kw))
    return root

def run_pipeline(root: Path):
    from qanat.project import load, validate
    from qanat.runner import run_all
    from qanat.store import Store
    p, r = load(root)
    rep = validate(p, r)
    store = Store(p.store_url(r))
    results = run_all(store, p, r)
    return p, r, store, rep, results

def backtest(store, p, r, frm, to, **kw):
    from qanat.backtest import run_backtest
    return run_backtest(store, p, r, frm, to, **kw)

# ---------------------------------------------------------------- price makers
def flat_then_drift(dates, symbols, drift):
    """Every symbol compounds at exactly `drift` per day. Nothing random."""
    rows = []
    for s in symbols:
        px = 100.0
        for d in dates:
            rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "close": round(px, 10)})
            px *= (1.0 + drift)
    return pd.DataFrame(rows)

def per_symbol_drift(dates, drifts: dict):
    rows = []
    for s, g in drifts.items():
        px = 100.0
        for d in dates:
            rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "close": round(px, 10)})
            px *= (1.0 + g)
    return pd.DataFrame(rows)

def gbm(dates, symbols, seed=1, mu=0.0, sigma=0.01):
    rng = np.random.default_rng(seed)
    rows = []
    for s in symbols:
        px = 100.0
        for d in dates:
            rows.append({"date": d.strftime("%Y-%m-%d"), "symbol": s, "close": round(px, 10)})
            px *= float(np.exp(rng.normal(mu, sigma)))
    return pd.DataFrame(rows)

DAYS = pd.bdate_range("2024-01-01", periods=260)
