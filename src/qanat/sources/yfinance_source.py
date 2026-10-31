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

**Neither close is the price that was on the screen that day, and the column
names say otherwise.** `auto_adjust=False` does not turn adjustment off. It
turns off the dividend half:

    close             adjusted for every later SPLIT
    adjusted_close    adjusted for every later split AND dividend

Both reach backwards. NVIDIA split 4-for-1 in 2021 and 10-for-1 in 2024, so
`close` for 2021-01-04 comes back as $13.11 against the $524.54 that actually
traded.

**That matters because a filing is never rewritten.** Dividing a restated price
by an EPS the SEC still reports as filed mixes two different footings, and the
answer is wrong by the split factor with nothing to show for it. NVIDIA's `pe`
for its 2022 filing comes out at 6.9 against a true 68.7 — and the error points
one way, so a value screen is pulled towards whatever split.

So `split_ratio` is stored beside the price. It is the ratio that took effect
on that day, 0 on every other day, and it is what lets either number be
rebuilt from the other:

    as printed  =  close x (every split_ratio after that date)

Which one a caller wants depends on the question. A ratio against a filing
figure -- `pe`, `pbr` -- needs the price as printed, because the other half
was never restated. A return needs the adjusted price, because a split is not
a loss.

Columns:

    subject         the ticker, as asked for
    date            the trading day
    close           split-adjusted. NOT the price that printed -- see above
    adjusted_close  split and dividend adjusted
    split_ratio     the split that took effect this day, else 0
    dividend        the dividend paid this day, else 0
    currency        from Yahoo's own metadata, not assumed
    source          'yfinance'
    fetched_at      when this row was pulled. `adjusted_close` keeps moving as
                    later dividends are paid, so a value with no fetch date
                    cannot be audited
    as_of           the trading day. A close is public when that day ends

**`adjusted_close` is stored rather than recomputed**, which is the opposite of
the rule for splits. Rebuilding it from the close and the dividends lands about
1.2% away from Yahoo's own number, because the detail of dividend adjustment
differs between vendors. A split is exact arithmetic and is recomputed; a
dividend adjustment is somebody's answer and is kept.

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


def fetch(source: Source, root: Path, on_warn=None, since: str | None = None) -> pd.DataFrame:
    yf = _require()
    opts: dict[str, Any] = source.options or {}
    companies = opts.get("companies") or []
    if not companies:
        raise ValueError(f"source '{source.id}': yfinance needs options.companies")
    start, end = opts.get("from"), opts.get("to")
    if not start:
        raise ValueError(f"source '{source.id}': yfinance needs options.from")

    # Close the gap the last poll left, rather than downloading the whole
    # history again. `options.from` is where a cold table starts; `since` is
    # the newest day already stored, and after that it is the one that counts.
    #
    # This is the difference between asking Yahoo for one day and asking for
    # twenty years of every company on every run. At a few names the second is
    # merely wasteful; at a few thousand it is the thing that gets refused.
    full = start
    if since:
        resume = pd.Timestamp(since).normalize()
        # One day back, not the day itself: the last stored day may have been
        # a partial bar if the poll ran before the close, and re-fetching it
        # costs one row and replaces it through the key.
        start = str((resume - pd.Timedelta(days=1)).date())
        if on_warn and pd.Timestamp(start) > pd.Timestamp(full):
            on_warn(f"resuming from {start} rather than {full}")

    fetched_at = pd.Timestamp.utcnow().tz_localize(None)
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
            # The event, not the adjustment. `history()` already carries both,
            # so this is a column to keep rather than a second request.
            "split_ratio": (hist["Stock Splits"].astype(float).to_numpy()
                            if "Stock Splits" in hist.columns else 0.0),
            "dividend": (hist["Dividends"].astype(float).to_numpy()
                         if "Dividends" in hist.columns else 0.0),
            "currency": currency,
            "source": SOURCE,
            "fetched_at": fetched_at,
        })
        # A close is knowable when the day it belongs to has ended, so the
        # trading day is also the as-of date.
        frame["as_of"] = frame["date"]
        rows.append(frame)

    if not rows:
        return pd.DataFrame(columns=[
            "subject", "date", "close", "adjusted_close", "split_ratio", "dividend",
            "currency", "source", "fetched_at", "as_of",
        ])
    out = pd.concat(rows, ignore_index=True)
    return out.sort_values(["subject", "date"]).reset_index(drop=True)


#: Tells `sources.fetch` this adapter takes a `since`. Without it the
#: dispatcher calls the old two-argument shape and the fetch stays full.
fetch.wants_since = True
