"""One close per filing: the price on the day the numbers became public.

A filing has a date. A market does not trade every day. So the rule, from
`filings-dataset/spec.md`, is the close on `published_at`, or the last trading
day before it when the filing landed on a weekend or a holiday.

**Which of those two happened is stored, not inferred later.** A reader who
finds a price dated three days before the filing cannot tell whether the market
was shut or the join was wrong. `priced_on` and `trading_day_gap` answer that
in the row.

The join from a price to a filing goes through `documents`, because
`prices.subject` is a ticker and `documents.entity` is a CIK. The ticker lives
in `source_keys`.

**Three prices come out, and the one you want depends on the question.**

    close_as_printed   what the share actually cost that day
    close              Yahoo's, already adjusted for every later split
    adjusted_close     Yahoo's, adjusted for later splits and dividends

A ratio against a filing figure -- `pe`, `pbr` -- needs `close_as_printed`,
because the other half of the ratio comes from a filing and the SEC never
rewrites one. Using `close` there divides a restated number by an original
one: NVIDIA split 4-for-1 and then 10-for-1, so its 2022 `pe` came out at 6.9
against a true 68.7, and the error points one way, which pulls a value screen
towards whatever split.

A return needs `adjusted_close`, because a split is not a loss.

`splits_since` is the factor that was undone, and it is 1 for most rows. It is
kept so the arithmetic can be checked rather than trusted.
"""

import json

import pandas as pd

COLUMNS = [
    "document", "subject", "ticker", "published_at", "priced_on",
    "trading_day_gap", "close_as_printed", "close", "adjusted_close",
    "splits_since", "currency", "source", "as_of",
]


def run(ctx):
    docs = ctx.read("raw.documents")
    prices = ctx.read("raw.prices")
    if docs.empty or prices.empty:
        return pd.DataFrame(columns=COLUMNS)

    prices = prices.copy()
    prices["date"] = pd.to_datetime(prices["date"]).dt.normalize()

    # Undo what the vendor did to history. A split on day D divides every price
    # before D, so multiplying a row by the splits that came after it gives
    # back the price that actually traded. Computed per company, walking
    # backwards from today, so one pass settles every row.
    prices = prices.sort_values(["subject", "date"])
    if "split_ratio" in prices.columns:
        ratio = prices["split_ratio"].fillna(0.0).replace(0.0, 1.0)
        # the product of every split strictly after this row
        rev = ratio[::-1].groupby(prices["subject"][::-1]).cumprod()[::-1]
        prices["splits_since"] = rev / ratio
    else:
        prices["splits_since"] = 1.0
    prices["close_as_printed"] = prices["close"] * prices["splits_since"]

    by_ticker = {t: g.sort_values("date") for t, g in prices.groupby("subject")}

    rows = []
    for doc in docs.itertuples():
        ticker = json.loads(doc.source_keys).get("ticker")
        series = by_ticker.get(ticker)
        if series is None:
            ctx.log(f"{doc.id}: no prices for {ticker}", "warn")
            continue
        filed = pd.Timestamp(doc.published_at).normalize()
        # Every trading day up to and including the filing date. The last one is
        # the answer: the filing date itself if the market was open, otherwise
        # the most recent day it was.
        upto = series[series["date"] <= filed]
        if upto.empty:
            ctx.log(f"{doc.id}: no price on or before {filed.date()}", "warn")
            continue
        hit = upto.iloc[-1]
        gap = int((filed - hit["date"]).days)
        if gap > 4:
            ctx.log(
                f"{doc.id}: nearest close is {gap} days before the filing "
                f"({hit['date'].date()}), which is longer than a weekend", "warn")
        rows.append({
            "document": doc.id,
            "subject": doc.entity,
            "ticker": ticker,
            "published_at": filed,
            "priced_on": hit["date"],
            # 0 means the market was open on the filing date.
            "trading_day_gap": gap,
            # The one a ratio against a filing figure must use.
            "close_as_printed": float(hit["close_as_printed"]),
            "close": float(hit["close"]),
            "adjusted_close": float(hit["adjusted_close"]),
            "splits_since": float(hit["splits_since"]),
            "currency": hit["currency"],
            "source": hit["source"],
            # The filing date, not the price date. This row becomes knowable
            # when the filing lands, which is the later of the two.
            "as_of": filed,
        })

    df = pd.DataFrame(rows, columns=COLUMNS)
    if not df.empty:
        shut = int((df["trading_day_gap"] > 0).sum())
        split = int((df["splits_since"] > 1.0).sum())
        ctx.log(f"{len(df)} filings priced, {shut} of them on a day the market was shut"
                + (f", {split} undone from a later split" if split else ""))
    return df
