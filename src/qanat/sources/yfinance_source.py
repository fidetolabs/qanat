"""Daily closes from Yahoo Finance, through the `yfinance` library.

    - id: prices
      to: [raw.prices]
      connector: yfinance
      mode: replace
      key: [subject, date]
      options:
        companies: [AAPL, MSFT]      # tickers, as Yahoo spells them
        from: "2021-01-01"
        to:   "2026-12-31"

Install with the extra: `pip install qanat-fdtl[prices]`.

**Read this before putting it in anything you sell.** `yfinance` is not an API
with a contract behind it. It reads Yahoo's own endpoints, and Yahoo's terms do
not grant redistribution. So every row this lands carries `source: yfinance`,
and that column is the thing a later cleanup acts on. A store that mixes these
rows in with licensed ones and does not record which is which cannot be
separated afterwards.

It is also unofficial, which has an operational cost separate from the legal
one: Yahoo changes its endpoints and the library catches up afterwards. A
pipeline that must not break on a Tuesday should not have this in it.

**Both closes are kept, and which is which is a column.** A split-adjusted
close and an unadjusted close are different numbers for the same day, and a
per-share figure built from one and an EPS from the other is wrong in a way no
check catches. `close` is the price as it printed. `adjusted_close` is Yahoo's,
restated for every split and dividend since, which means it is **not** the
number that was on the screen that day. For a point-in-time read, use `close`.

Columns:

    subject         the ticker, as asked for
    date            the trading day
    close           the close as it printed that day
    adjusted_close  restated for later splits and dividends
    currency        from Yahoo's own metadata, not assumed
    source          'yfinance'
    as_of           the trading day. A close is public when that day ends

`subject` is a ticker and `facts.subject` is a regulator's id, so the two do not
join directly. Go through the documents table, which carries both.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from qanat.models import Source

SOURCE = "yfinance"


def _require():
    try:
        import yfinance
    except ModuleNotFoundError:
        raise ValueError(
            "the yfinance connector needs the `prices` extra: "
            "pip install 'qanat-fdtl[prices]'"
        ) from None
    return yfinance


def fetch(source: Source, root: Path, on_warn=None) -> pd.DataFrame:
    yf = _require()
    opts: dict[str, Any] = source.options or {}
    companies = opts.get("companies") or []
    if not companies:
        raise ValueError(f"source '{source.id}': yfinance needs options.companies")
    start, end = opts.get("from"), opts.get("to")
    if not start:
        raise ValueError(f"source '{source.id}': yfinance needs options.from")

    rows: list[pd.DataFrame] = []
    for ticker in companies:
        t = yf.Ticker(str(ticker))
        # auto_adjust=False so the printed close survives. With it on, `Close`
        # is silently the adjusted one and the distinction above is gone.
        hist = t.history(start=start, end=end, auto_adjust=False)
        if hist.empty:
            if on_warn:
                on_warn(f"{ticker}: Yahoo returned no rows for {start}..{end}")
            continue
        # fast_info is a second network call and is not worth failing a whole
        # fetch over. An empty currency is visible to a reader; a guessed one
        # is not, so nothing is assumed here.
        currency = ""
        try:
            currency = (t.fast_info.get("currency") or "").upper()
        except Exception as exc:  # noqa: BLE001 - yfinance raises whatever it likes
            if on_warn:
                on_warn(f"{ticker}: no currency from Yahoo ({type(exc).__name__})")
        frame = pd.DataFrame({
            "subject": str(ticker),
            # Yahoo returns a tz-aware index in the exchange's own zone. The
            # calendar day is what a filing date joins against, so the clock
            # time goes.
            "date": hist.index.tz_localize(None).normalize()
            if hist.index.tz is None else hist.index.tz_convert(None).normalize(),
            "close": hist["Close"].astype(float).to_numpy(),
            "adjusted_close": hist["Adj Close"].astype(float).to_numpy()
            if "Adj Close" in hist.columns else hist["Close"].astype(float).to_numpy(),
            "currency": currency,
            "source": SOURCE,
        })
        # A close is knowable when the day it belongs to has ended, so the
        # trading day is also the as-of date.
        frame["as_of"] = frame["date"]
        rows.append(frame)

    if not rows:
        return pd.DataFrame(columns=[
            "subject", "date", "close", "adjusted_close", "currency", "source", "as_of",
        ])
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["subject", "date"]).reset_index(drop=True)
