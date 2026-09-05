"""Low volatility — hold the quiet names

Rank by realised volatility, keep the calmest, and size each one inversely to its
own volatility so no single name dominates the book.

Wired by `qanat alphas add low_vol`. It is an ordinary step -- edit it, or throw it
away and write your own. That is the point of having it.
"""

import pandas as pd


def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \
        o.get("price_column", "close")
    window, top_n = int(o.get("window", 60)), int(o.get("top_n", 4))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()["symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        r = g[px].pct_change().dropna().tail(window)
        if len(r) < window // 2:
            continue
        vol = float(r.std())
        if vol > 0:
            rows.append({"symbol": name, "score": vol, "as_of": g[date].iloc[-1]})
    if len(rows) < top_n:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    # quietest names, then size each inversely to its own volatility
    picked = pd.DataFrame(rows).nsmallest(top_n, "score").copy()
    inv = 1.0 / picked["score"]
    picked["weight"] = inv / inv.sum()
    return picked[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
