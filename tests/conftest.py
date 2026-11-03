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
import os
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
    description: operator outputs -- a feature transform may read another feature transform
  - id: metrics
    kind: metrics
    description: one number per subject per date, screenable. The stage is the registry

# ------------------------------------------------------- the ingestion jobs --
# What to fetch, and the one table it lands in. No cadence here -- that is a
# `job_schedules:` row pointing at the id.
ingestion_job_definitions:
  - id: daily_prices
    to: [raw.daily_prices]
    connector: seed
    key: [symbol, ts]
    options:
      path: ./seed/daily_prices.csv
      parse_dates: [ts]

  - id: news
    to: [raw.news]
    connector: seed
    key: [symbol, ts]
    options:
      path: ./seed/news.csv
      parse_dates: [ts]

# ----------------------------------------------------------- the transforms --
# n:m -- `from` and `to` are both lists.
transform_job_definitions:
  - id: normalize
    from: [raw.daily_prices]
    to:   [normalized.prices]
    script: transforms/normalize.sql

  - id: momentum
    from: [normalized.prices]
    to:   [features.momentum]
    script: transforms/momentum.py
    options:
      lookback: 20

  - id: risk
    from: [normalized.prices]
    to:   [features.risk]
    script: transforms/risk.sql
    options:
      window: 60

  - id: tone
    from: [raw.news]
    to:   [features.tone]
    script: transforms/tone.sql

  - id: portfolio
    from: [features.momentum, features.risk, features.tone]
    to:   [features.target]
    script: transforms/portfolio.py
    options:
      top_n: 4
      basket: ./universes/demo8.csv

  # A metric: one number per subject per date, in its own table in the metrics
  # stage. `key:` is what makes it one -- an ordinary derived table is rewritten
  # because it is a function of what is upstream, and a metric value is not: it
  # is what somebody was shown.
  - id: vol
    from: [features.risk]
    to:   [metrics.vol]
    script: transforms/metrics/vol.sql
    key:  [subject, date]
    options:
      unit: ratio
      description: annualised realised volatility, as a screenable metric

# ------------------------------------------------------------ the schedules --
# One cron line each, over one definition, and one schedule per definition.
# `daily_prices` carried a second at `*/30` to show a definition holding two
# cadences; that is refused as of 0.12.0, because over one selection the finer
# cron already does everything the coarser one would.
job_schedules:
  - {id: daily_prices_2m,  definition: daily_prices, cron: "*/2 * * * *"}
  - {id: news_5m,          definition: news,         cron: "*/5 * * * *"}
  - {id: normalize_2m,     definition: normalize,    cron: "*/2 * * * *"}
  - {id: momentum_3m,      definition: momentum,     cron: "*/3 * * * *"}
  - {id: risk_3m,          definition: risk,         cron: "*/3 * * * *"}
  - {id: tone_5m,          definition: tone,         cron: "*/5 * * * *"}
  - {id: portfolio_5m,     definition: portfolio,    cron: "*/5 * * * *"}

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
    import pathlib

    top_n = int(ctx.options.get("top_n", 4))
    # The basket, read off the csv by path. There is no registry to look it up
    # in any more: a universe is a file a connector can point at, and a
    # transform that wants one reads it the same way.
    held = pd.read_csv(pathlib.Path(ctx.root) / ctx.options["basket"])

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

VOL_SQL = """\
-- vol -- a metric reading a feature.
--
-- The feature is the operator's working table; the metric is the one number per
-- subject per date that a clause can compare. Both are the operator's
-- judgement, which is why they are not in `normalized` -- the difference is
-- shape, and only the metric's shape is screenable.
SELECT symbol             AS subject,
       CAST(as_of AS DATE) AS date,
       vol_annual         AS value
FROM features__risk
WHERE vol_annual IS NOT NULL
"""

FILES = {
    "qanat.yaml": QANAT_YAML,
    "universes/demo8.csv": SYMBOLS,
    "transforms/normalize.sql": NORMALIZE_SQL,
    "transforms/risk.sql": RISK_SQL,
    "transforms/metrics/vol.sql": VOL_SQL,
    "transforms/tone.sql": TONE_SQL,
    "transforms/momentum.py": MOMENTUM_PY,
    "transforms/portfolio.py": PORTFOLIO_PY,
}

SYMBOL_LIST = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "SPY"]


# ------------------------------------------------------------ the `seed` connector --
# **The suite brings its own connector, because the product ships two and both
# need the network.** `sec` is EDGAR and `yfinance` is a market feed; a test can
# have neither, and the shape a test asserts on -- a definition, a key, a table
# it lands in -- is the same whatever is upstream of it.
#
# This was the `csv` connector, which shipped until 0.12.0 and was used by
# nothing but these tests. **A test needing a cheap adapter is not a reason for
# a product to offer one**, so it moved in here, where naming it `seed` says
# what it is for. Registering it is the whole plugin surface: a name in
# `ADAPTERS` and a `fetch(job, root)`.
_SEED_READ_CSV = (
    "sep", "header", "names", "usecols", "dtype", "skiprows", "nrows",
    "na_values", "parse_dates", "thousands", "decimal", "encoding",
)


def _seed_fetch(job, root: Path, on_warn=None) -> pd.DataFrame:
    path = (job.options or {}).get("path")
    if not path:
        raise ValueError(f"ingestion job '{job.id}': seed needs options.path")
    kw = {k: v for k, v in (job.options or {}).items() if k in _SEED_READ_CSV}
    return pd.read_csv(root / path, **kw)


@pytest.fixture(autouse=True)
def seed_connector():
    """`connector: seed` works for the length of one test and no longer.

    Restored afterwards rather than registered once at import, so nothing the
    suite does to the roster outlives the test that did it. **A test asserting
    on what ships has to subtract this one by name** -- `set(ADAPTERS) -
    {"seed"}` -- because autouse means it is there during every test.
    """
    from qanat.connectors import ADAPTERS

    before = dict(ADAPTERS)
    ADAPTERS["seed"] = _seed_fetch
    yield
    ADAPTERS.clear()
    ADAPTERS.update(before)


#: Run the whole suite against a parquet store with
#: `QANAT_TEST_STORE=parquet uv run pytest`. The point is equivalence: the same
#: tests, the same assertions, a different storage kind underneath.
STORE_KIND = os.environ.get("QANAT_TEST_STORE", "duckdb")


def split_project(root: Path) -> Path:
    """Put a one-blob `qanat.yaml` into the files the project actually has.

    **The tests write one blob on purpose.** A fixture is about the project, not
    about which file each key lives in, and spelling the split out in forty of
    them would make every one of them a worse description of what it is testing.
    `project_io` owns the layout, so this asks it which file each key belongs to
    and moves only those, leaving the rest where the fixture wrote it.

    **Through the round-tripper, not `safe_dump`.** A fixture whose point is that
    a note in `qanat.yaml` survives cannot be set up by a splitter that strips
    notes -- that failure would look like the engine losing them.
    """
    import yaml

    from qanat.project_io import PARTS, _round_tripper, part_for

    path = root / "qanat.yaml"
    if not path.is_file():
        return root

    yml = _round_tripper()
    if yml is not None:
        with path.open() as fh:
            whole = yml.load(fh) or {}
    else:
        whole = yaml.safe_load(path.read_text()) or {}

    moved = {k: whole.pop(k) for k in list(whole) if part_for(k)}
    if not moved:
        return root

    def _write(target: Path, data: object) -> None:
        if yml is not None:
            with target.open("w") as fh:
                yml.dump(data, fh)
        else:
            target.write_text(yaml.safe_dump(data, sort_keys=False))

    _write(path, whole)
    for name in PARTS:
        part = {k: v for k, v in moved.items() if part_for(k) == name}
        if part:
            _write(root / name, part)
    return root


def write_project_text(root: Path, text: str) -> Path:
    """One YAML blob on disk, split into the files that own each key."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "qanat.yaml").write_text(text)
    return split_project(root)


def edit_project(root: Path, old: str, new: str) -> Path:
    """Change a line wherever it lives: `qanat.yaml` or one of the part files.

    A test editing a definition should not have to know which file holds it --
    that is the thing the split moved, and the test is about the change.
    """
    for name in ("qanat.yaml", "jobs.yaml"):
        path = root / name
        if path.is_file() and old in (text := path.read_text()):
            path.write_text(text.replace(old, new))
            return path
    raise AssertionError(f"{old!r} is in none of this project's files")


def write_demo_project(target: Path, name: str = "demo", store: str | None = None) -> Path:
    """Write the fixture project and its two seed files. Nothing runs yet."""
    if store is None:
        store = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    store_line = f"store: {store}"
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
    return split_project(target)


@pytest.fixture
def demo_project(tmp_path: Path) -> Path:
    return write_demo_project(tmp_path)
