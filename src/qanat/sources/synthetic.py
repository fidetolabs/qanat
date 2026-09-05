"""A market that is not real, so a new project runs green before any key exists.

    connector: synthetic
    options:
      series: prices | news
      symbols: [AAPL, MSFT]         # or `from_universe: ./universes/sp100.csv`
      days: 400

The numbers are generated from a fixed seed, so two runs of the same project
produce the same history. Nothing here reaches the network.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from qanat.models import Source

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


def _symbols(source: Source, root: Path) -> list[str]:
    o = source.options
    if syms := o.get("symbols"):
        return list(syms)
    if path := o.get("from_universe"):
        return pd.read_csv(root / path)["symbol"].tolist()
    return ["AAPL", "MSFT", "NVDA", "AMZN", "META", "JPM", "XOM", "SPY"]


def fetch(source: Source, root: Path) -> pd.DataFrame:
    o = source.options
    series = o.get("series", "prices")
    if series == "bars":
        series = "prices"   # the name this used to go by
    symbols = _symbols(source, root)
    days = int(o.get("days", 400))
    end = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    dates = [end - timedelta(days=d) for d in range(days)][::-1]

    if series == "news":
        rows = []
        for sym in symbols:
            rng = np.random.default_rng(_seed(sym + "news"))
            for ts in dates[-60:]:
                if rng.random() > 0.12:
                    continue
                rows.append({
                    "ts": ts,
                    "symbol": sym,
                    "headline": HEADLINES[rng.integers(len(HEADLINES))].format(sym=sym),
                    "sentiment": round(float(rng.normal(0, 0.45)), 4),
                    "source": ["wire", "blog", "filing"][int(rng.integers(3))],
                })
        return pd.DataFrame(rows)

    rows = []
    for sym in symbols:
        rng = np.random.default_rng(_seed(sym))
        px = 40 + rng.random() * 400
        drift = rng.normal(0.0003, 0.0004)
        vol = 0.012 + rng.random() * 0.02
        base_vol = 1e6 * (1 + rng.random() * 8)
        for ts in dates:
            ret = rng.normal(drift, vol)
            px = max(1.0, px * (1 + ret))
            hi = px * (1 + abs(rng.normal(0, vol / 3)))
            lo = px * (1 - abs(rng.normal(0, vol / 3)))
            rows.append({
                "ts": ts,
                "symbol": sym,
                "open": round(px / (1 + ret), 4),
                "high": round(hi, 4),
                "low": round(lo, 4),
                "close": round(px, 4),
                "volume": int(base_vol * (0.6 + abs(rng.normal(1, 0.35)))),
            })
    return pd.DataFrame(rows)
