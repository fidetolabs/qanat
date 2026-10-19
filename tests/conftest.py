"""A runnable project for the tests, built without touching the network.

`qanat init` scaffolds against SEC EDGAR, which is real and therefore needs a
network and a contact address. A test suite can have neither, so this fixture
writes the same *shape* of project -- prices and news, normalised, three
features, a portfolio -- over two CSV files it generates itself.

**This data is invented, and that is allowed here in a way it is not in
`examples/`.** An example is a claim about what the tool does with real data;
a fixture is a controlled input for asserting on behaviour. The generator is
seeded, so the same test sees the same history on every machine.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HEADLINES = [
    "{sym} beats on revenue, guides higher",
    "{sym} cut to hold on valuation",
    "regulator opens review touching {sym}",
    "{sym} announces buyback",
    "supply chain easing lifts {sym} outlook",
    "insider selling reported at {sym}",
]


def _seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


def make_prices(symbols: list[str], days: int = 420) -> pd.DataFrame:
    """A geometric random walk per symbol, seeded off the ticker."""
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for sym in symbols:
        rng = np.random.default_rng(_seed(sym))
        price = 50.0 + rng.random() * 150.0
        drift = (rng.random() - 0.45) * 0.0012
        vol = 0.008 + rng.random() * 0.016
        for i in range(days):
            ts = end - timedelta(days=days - i)
            if ts.weekday() >= 5:
                continue
            price *= float(np.exp(drift + vol * rng.standard_normal()))
            high = price * (1 + abs(rng.standard_normal()) * vol * 0.4)
            low = price * (1 - abs(rng.standard_normal()) * vol * 0.4)
            rows.append({
                "ts": ts.date().isoformat(), "symbol": sym,
                "open": round(price * (1 + rng.standard_normal() * vol * 0.2), 4),
                "high": round(high, 4), "low": round(low, 4), "close": round(price, 4),
                "volume": int(1e6 + rng.random() * 9e6),
            })
    return pd.DataFrame(rows)


def make_news(symbols: list[str], days: int = 60) -> pd.DataFrame:
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for sym in symbols:
        rng = np.random.default_rng(_seed(sym + "news"))
        for i in range(days):
            if rng.random() > 0.35:
                continue
            ts = end - timedelta(days=days - i)
            rows.append({
                "ts": ts.date().isoformat(), "symbol": sym,
                "headline": HEADLINES[int(rng.integers(len(HEADLINES)))].format(sym=sym),
                "sentiment": round(float(rng.standard_normal() * 0.4), 4),
            })
    return pd.DataFrame(rows)


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
# Order here is order in the pipeline. The names are the project's own; the
# platform enforces properties, not names.
stages:
  - id: raw
    kind: raw
    description: landed exactly as it arrived, never edited
  - id: normalized
    kind: normalized
    description: typed, deduplicated, one key set
  - id: features
    kind: features
    description: operator outputs -- a feature step may read another feature step

# ---------------------------------------------------------------- the sources --
# Many sources may feed one stage; each writes its own table.
sources:
  - id: daily_prices
    to: [raw.daily_prices]
    connector: csv
    mode: replace
    schedule: "*/2 * * * *"
    options:
      path: ./seed/daily_prices.csv
      parse_dates: [ts]

  - id: news
    to: [raw.news]
    connector: csv
    mode: replace
    schedule: "*/5 * * * *"
    options:
      path: ./seed/news.csv
      parse_dates: [ts]

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
    to:   [features.target]
    script: steps/portfolio.py
    universe: demo8
    schedule: "*/5 * * * *"
    options:
      top_n: 4

"""

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

FILES = {
    "qanat.yaml": QANAT_YAML,
    "universes/demo8.csv": SYMBOLS,
    "steps/normalize.sql": NORMALIZE_SQL,
    "steps/risk.sql": RISK_SQL,
    "steps/tone.sql": TONE_SQL,
    "steps/momentum.py": MOMENTUM_PY,
    "steps/portfolio.py": PORTFOLIO_PY,
}

SYMBOL_LIST = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "SPY"]


def write_demo_project(target: Path, name: str = "demo", store: str | None = None) -> Path:
    """Write the fixture project and its two seed files. Nothing runs yet."""
    store_line = f"store: {store}" if store else "store: ./data/qanat.duckdb"
    for rel, text in FILES.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        body = text.replace("{name}", name)
        if rel == "qanat.yaml":
            body = body.replace("store: ./data/qanat.duckdb", store_line)
        path.write_text(body)
    seed = target / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    make_prices(SYMBOL_LIST).to_csv(seed / "daily_prices.csv", index=False)
    make_news(SYMBOL_LIST).to_csv(seed / "news.csv", index=False)
    (target / "data").mkdir(exist_ok=True)
    return target


@pytest.fixture
def demo_project(tmp_path: Path) -> Path:
    return write_demo_project(tmp_path)
