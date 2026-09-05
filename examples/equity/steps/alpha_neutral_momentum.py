"""Market-neutral momentum — long the strong, short the weak

Momentum with the average taken out, so the portfolio is not just a bet that the
market goes up. Long and short sides are equal, |weights| sum to 1.

Wired by `qanat alphas add neutral_momentum`. It is an ordinary step -- edit it, or throw it
away and write your own. That is the point of having it.
"""

import pandas as pd


def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \
        o.get("price_column", "close")
    lookback = int(o.get("lookback", 60))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()["symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        if len(g) <= lookback:
            continue
        past, now = g[px].iloc[-lookback - 1], g[px].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": name, "score": now / past - 1.0, "as_of": g[date].iloc[-1]})
    if len(rows) < 3:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    df = pd.DataFrame(rows)
    # centre the scores, so the book is long the above-average and short the rest,
    # and scale so |weights| sum to 1 -- the size of the book never depends on how
    # strong the signal happened to be that day
    z = df["score"] - df["score"].mean()
    scale = z.abs().sum()
    if scale == 0:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])
    df["weight"] = z / scale
    return df[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
