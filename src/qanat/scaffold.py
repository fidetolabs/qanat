"""`qanat init` -- a project that runs green before any API key exists.

The starter uses the synthetic source, so a new user sees a full pipeline
(sources -> raw -> normalized -> features -> weights) on the first run, with no
network and no credentials. Swapping in a real feed is one block of YAML.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

QANAT_YAML = """\
# What this pipeline is. `qanat check` enforces the rules; see docs/contract.md
project: {name}
store: ./data/qanat.duckdb

# -------------------------------------------------------------- the universes --
# Which symbols a portfolio is allowed to hold.
universes:
  - id: demo8
    index: DEMO 8
    symbols: ./universes/demo8.csv

# ------------------------------------------------------------------ the stages --
# Order here is order in the pipeline. `weights` is last, unless a `pnl` stage
# follows it -- that one is written by `qanat backtest`, never by a step.
stages:
  - id: raw
    kind: raw
    description: landed exactly as it arrived, never edited
  - id: normalized
    kind: features
    description: typed, deduplicated, one key set
  - id: features
    kind: features
    description: operator outputs -- a feature step may read another feature step
  - id: weights
    kind: weights
    description: one table per alpha -- a target weight per symbol, no budget
  - id: pnl
    kind: pnl
    description: what each alpha earned, per rebalance. Written by `qanat backtest`

# ---------------------------------------------------------------- the sources --
# Many sources may feed one stage; each writes its own table.
sources:
  - id: daily_prices
    to: [raw.daily_prices]
    connector: synthetic     # swap for rest, sql or csv when you have a feed
    mode: replace
    schedule: "*/2 * * * *"
    options:
      series: bars
      from_universe: ./universes/demo8.csv
      days: 420

  - id: news
    to: [raw.news]
    connector: synthetic
    mode: replace
    schedule: "*/5 * * * *"
    options:
      series: news
      from_universe: ./universes/demo8.csv

# ------------------------------------------------------------------ the steps --
# n:m -- `from` and `to` are both lists.
steps:
  - id: normalize
    from: [raw.daily_prices]
    to:   [normalized.prices]
    script: steps/normalize.sql
    schedule: "*/2 * * * *"

  - id: momentum
    from: [normalized.prices]
    to:   [features.momentum]
    script: steps/momentum.py
    schedule: "*/3 * * * *"
    options:
      lookback: 20

  - id: risk
    from: [normalized.prices]
    to:   [features.risk]
    script: steps/risk.sql
    schedule: "*/3 * * * *"
    options:
      window: 60

  - id: tone
    from: [raw.news]
    to:   [features.tone]
    script: steps/tone.sql
    schedule: "*/5 * * * *"

  - id: portfolio
    from: [features.momentum, features.risk, features.tone]
    to:   [weights.target]
    script: steps/portfolio.py
    universe: demo8
    schedule: "*/5 * * * *"
    options:
      top_n: 4

# ---------------------------------------------------------------- the backtest --
# The weights table says what to hold. This says what holding it costs, so the
# number `qanat backtest` prints is net -- gross return minus fees and slippage.
#
#   qanat backtest --from 2026-01-05 --to 2026-06-01 --rebalance 10d
backtest:
  prices: normalized.prices     # where a symbol's price per day comes from
  price_column: close
  symbol_column: symbol
  date_column: date
  fee_bps: 5                  # charged on turnover
  slippage_bps: 10            # estimated, charged on turnover too
  rebalance: 5d               # gap between as-of dates
  purge: 0d                   # hold rows back this long before a step may read them
  embargo: 0d                 # wait this long after as_of before a return counts
"""

# `from` and `to` are what make this universe point-in-time: a symbol counts on a
# date only if it had joined by then and had not yet left. Without them a backtest
# holds today's list across the whole window, which flatters every number it prints
# -- the survivorship bias. Blank `from` means since always; blank `to` means still
# in. DELS is here to show the shape: a name that left partway through.
SYMBOLS = """\
symbol,name,sector,from,to
AAPL,Apple,TECH,,
MSFT,Microsoft,TECH,,
NVDA,Nvidia,SEMI,,
AMZN,Amazon,RETAIL,,
META,Meta,MEDIA,,
JPM,JPMorgan,BANK,,
XOM,Exxon,ENERGY,,
SPY,S&P 500 ETF,INDEX,,
"""

NORMALIZE_SQL = """\
-- raw -> normalized. Type it, dedupe it, conform it to one key set.
-- Tables are addressed as `stage__table`.
SELECT
    CAST(ts AS DATE)        AS date,
    symbol,
    CAST(open   AS DOUBLE)  AS open,
    CAST(high   AS DOUBLE)  AS high,
    CAST(low    AS DOUBLE)  AS low,
    CAST(close  AS DOUBLE)  AS close,
    CAST(volume AS BIGINT)  AS volume
FROM raw__daily_prices
QUALIFY row_number() OVER (PARTITION BY symbol, CAST(ts AS DATE) ORDER BY ts DESC) = 1
"""

RISK_SQL = """\
-- Realised volatility over ${window} sessions, annualised.
WITH r AS (
    SELECT date, symbol,
           close / lag(close) OVER (PARTITION BY symbol ORDER BY date) - 1 AS ret
    FROM normalized__prices
)
SELECT
    symbol,
    max(date)                                   AS as_of,
    stddev_samp(ret) * sqrt(252)                AS vol_annual,
    count(*)                                    AS observations
FROM (
    SELECT * FROM r
    QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY date DESC) <= ${window}
)
WHERE ret IS NOT NULL
GROUP BY symbol
"""

TONE_SQL = """\
-- Sentiment, collapsed to one row per symbol.
SELECT
    symbol,
    max(ts)              AS as_of,
    avg(sentiment)       AS tone,
    count(*)             AS articles
FROM raw__news
GROUP BY symbol
"""

MOMENTUM_PY = '''\
"""Price momentum -- the Python side of a feature step.

A step receives one argument, ctx, and returns either a DataFrame (when it
writes one table) or {table: DataFrame} (when it writes several).
"""


def run(ctx):
    lookback = int(ctx.options.get("lookback", 20))
    bars = ctx.read("normalized.prices").sort_values(["symbol", "date"])

    out = []
    for symbol, g in bars.groupby("symbol"):
        if len(g) <= lookback:
            ctx.log(f"{symbol}: only {len(g)} sessions, needs {lookback + 1}", "warn")
            continue
        close = g["close"].to_numpy()
        out.append({
            "symbol": symbol,
            "as_of": g["date"].iloc[-1],
            "momentum": float(close[-1] / close[-1 - lookback] - 1),
            "lookback": lookback,
        })

    import pandas as pd
    return pd.DataFrame(out)
'''

PORTFOLIO_PY = '''\
"""The last stage: one table, one portfolio.

Target weights only -- no budget, no share counts. What size to trade is the
question the stage above this one asks, not this one.
"""

import pandas as pd


def run(ctx):
    top_n = int(ctx.options.get("top_n", 4))
    held = ctx.universe()                             # the symbols we may hold

    df = (
        ctx.read("features.momentum")[["symbol", "as_of", "momentum"]]
        .merge(ctx.read("features.risk")[["symbol", "vol_annual"]], on="symbol")
        .merge(ctx.read("features.tone")[["symbol", "tone"]], on="symbol", how="left")
    )
    df = df[df["symbol"].isin(held["symbol"])]
    df["tone"] = df["tone"].fillna(0.0)

    # risk-adjusted momentum, nudged by tone
    df["score"] = df["momentum"] / df["vol_annual"].clip(lower=1e-6) + 0.25 * df["tone"]
    picked = df.nlargest(top_n, "score").copy()
    if picked.empty:
        ctx.log("nothing scored -- writing an empty portfolio", "warn")
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    edge = picked["score"].clip(lower=0.0)
    picked["weight"] = (edge / edge.sum()) if edge.sum() > 0 else 1.0 / len(picked)

    ctx.log(f"held {len(picked)} of {len(df)} scored symbols")
    return picked[["symbol", "weight", "score", "momentum", "vol_annual", "tone", "as_of"]]
'''

README = """\
# {name}

A qanat project. Five stages, and the last one is a portfolio.

    qanat check      # does the pipeline obey the stage contract?
    qanat run        # poll every source, run every step, once
    qanat serve      # scheduler + console on http://127.0.0.1:8420

The sources are synthetic, so this runs with no network and no keys. Point
`sources:` at a real feed when you have one -- nothing else has to change.
"""

FILES = {
    "qanat.yaml": QANAT_YAML,
    "README.md": README,
    "universes/demo8.csv": SYMBOLS,
    "steps/normalize.sql": NORMALIZE_SQL,
    "steps/risk.sql": RISK_SQL,
    "steps/tone.sql": TONE_SQL,
    "steps/momentum.py": MOMENTUM_PY,
    "steps/portfolio.py": PORTFOLIO_PY,
}


#: What `--demo` adds to the starter project. Four alphas, so the console opens on
#: a book rather than a single card, and so the graph on the README is the graph
#: you actually get. Every one is an ordinary step reading an ordinary table.
DEMO_ALPHAS = ("momentum", "reversal", "low_vol", "neutral_momentum")


def add_shelf_alphas(root: Path, names: Sequence[str] = DEMO_ALPHAS,
                     reads: str = "normalized.prices") -> list[str]:
    """Wire shelf alphas into a project that is already on disk.

    Each becomes a step like any other: its own script under `steps/`, its own
    weights table, its own line in `qanat.yaml`. Nothing about them is special
    once they are written, which is the point of the shelf.
    """
    from qanat import alphas
    from qanat.editor import save_step
    from qanat.project import load

    project, root = load(root)
    weights = project.weights_stage
    if weights is None:
        raise ValueError("this project has no weights stage, so there is nowhere for an alpha to go")
    universe = next((st.universe for st in project.steps if st.universe), None) \
        or (project.universes[0].id if project.universes else None)

    added = []
    for name in names:
        step_id = f"alpha_{name}"
        if project.job(step_id) is not None:
            continue
        script = alphas.write_alpha(root, name)
        opts = dict(alphas.CATALOGUE[name]["options"])
        opts["reads"] = reads
        save_step(project, root, {
            "id": step_id, "from": [reads], "to": [f"{weights.id}.{name}"],
            "script": script, "universe": universe, "options": opts,
        }, create_script=False)
        project, root = load(root)
        added.append(step_id)
    return added


def write_project(target: Path, name: str, force: bool = False, postgres: bool = False,
                  store: str | None = None) -> list[Path]:
    written = []
    if store:
        store_line = f"store: {store}"
    elif postgres:
        store_line = "store: postgresql://qanat:qanat@localhost:5432/qanat"
    else:
        store_line = "store: ./data/qanat.duckdb"
    yaml_body = QANAT_YAML.replace("store: ./data/qanat.duckdb", store_line)
    files = {**FILES, "qanat.yaml": yaml_body}
    for rel, body in files.items():
        path = target / rel
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.replace("{name}", name))
        written.append(path)
    (target / "data").mkdir(exist_ok=True)
    return written
