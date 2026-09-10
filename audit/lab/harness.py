"""Build a whole qanat project from nothing, run it, and price it.

Everything here goes through the ordinary user path -- write a `qanat.yaml`, point
a source at a file, run the graph, replay it. No internal shortcuts, because a
shortcut is exactly where a real user's problem would hide.

    from lab import data, harness

    root = harness.build(tmp, "h1", data.drift(data.calendar(), {"A": 0.001}),
                         harness.HOLD_ONE.format(symbol="A"))
    p, r, store = harness.run(root)
    bt = harness.backtest(store, p, r, "2024-01-10", "2024-06-01")
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

NORMALIZE = """SELECT CAST(date AS DATE) AS date, symbol, CAST(close AS DOUBLE) AS close
FROM raw__bars
QUALIFY row_number() OVER (PARTITION BY symbol, CAST(date AS DATE) ORDER BY date DESC) = 1
"""

#: The smallest honest alpha: hold one name, always. Its answer is arithmetic.
HOLD_ONE = '''import pandas as pd


def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    return pd.DataFrame([{{"symbol": "{symbol}", "weight": 1.0, "as_of": df["date"].max()}}])
'''

#: Equal weight across whatever the universe allows -- the shape that shows what
#: happens when one of those names stops being priced.
EQUAL_WEIGHT = '''import pandas as pd


def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    syms = sorted(ctx.universe()["symbol"])
    return pd.DataFrame([{"symbol": s, "weight": 1.0 / len(syms), "as_of": df["date"].max()}
                         for s in syms])
'''


def project_yaml(name: str, *, rebalance: str = "1d", fee: float = 0.0, slip: float = 0.0,
                 purge: str = "0d", embargo: str = "0d", extra_steps: str = "",
                 alpha_script: str = "steps/alpha.py") -> str:
    lines = [
        f"project: {name}",
        "store: ./data/q.duckdb",
        "universes:",
        "  - {id: u, index: TEST, symbols: ./universes/u.csv}",
        "stages:",
        "  - {id: raw, kind: raw}",
        "  - {id: normalized, kind: features}",
        "  - {id: weights, kind: weights}",
        "  - {id: pnl, kind: pnl}",
        "sources:",
        "  - {id: bars, to: [raw.bars], connector: csv, mode: replace,",
        "     options: {path: ./seed/bars.csv}}",
        "steps:",
        "  - {id: normalize, from: [raw.bars], to: [normalized.prices],",
        "     script: steps/normalize.sql}",
        "  - id: alpha_h",
        "    from: [normalized.prices]",
        "    to: [weights.h]",
        f"    script: {alpha_script}",
        "    universe: u",
    ]
    if extra_steps:
        lines += extra_steps.rstrip("\n").split("\n")
    lines += [
        "backtest:",
        "  prices: normalized.prices",
        "  price_column: close",
        "  symbol_column: symbol",
        "  date_column: date",
        f'  rebalance: "{rebalance}"',
        f"  fee_bps: {fee}",
        f"  slippage_bps: {slip}",
        f'  purge: "{purge}"',
        f'  embargo: "{embargo}"',
    ]
    return "\n".join(lines) + "\n"


def build(where: Path, name: str, bars: pd.DataFrame, alpha_src: str,
          symbols: list[str] | None = None, files: dict[str, str] | None = None,
          **kw) -> Path:
    """A complete project on disk. `where` should be a tmp_path -- nothing here is
    meant to be kept, and a generated project committed to the repo is exhaust."""
    root = Path(where) / name
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
    for fname, body in (files or {}).items():
        (root / "steps" / fname).write_text(body)
    (root / "qanat.yaml").write_text(project_yaml(name, **kw))
    return root


def run(root: Path):
    """Load, check and run one pass. Returns (project, root, store, results)."""
    from qanat.project import load, validate
    from qanat.runner import run_all
    from qanat.store import Store

    project, r = load(root)
    report = validate(project, r)
    store = Store(project.store_url(r))
    return project, r, store, report, run_all(store, project, r)


def backtest(store, project, root, frm: str, to: str, **kw):
    from qanat.backtest import run_backtest

    return run_backtest(store, project, root, frm, to, **kw)
