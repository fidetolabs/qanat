"""Momentum — hold what has been going up

The oldest cross-sectional rule there is: rank by trailing return and hold the
top names, equally weighted. Long only, weights sum to 1.

Wired by `qanat alphas add momentum`. It is an ordinary step -- edit it, or throw it
away and write your own. That is the point of having it.
"""

import pandas as pd


def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \
        o.get("price_column", "close")
    lookback, top_n = int(o.get("lookback", 60)), int(o.get("top_n", 4))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()[sym if sym in ctx.universe().columns else "symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        if len(g) <= lookback:
            continue
        past, now = g[px].iloc[-lookback - 1], g[px].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": name, "score": now / past - 1.0, "as_of": g[date].iloc[-1]})
    if len(rows) < top_n:
        ctx.log(f"only {len(rows)} symbols have {lookback} days of history", "warn")
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    picked = pd.DataFrame(rows).nlargest(top_n, "score")
    picked["weight"] = 1.0 / len(picked)
    return picked[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
