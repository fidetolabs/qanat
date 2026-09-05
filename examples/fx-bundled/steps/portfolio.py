"""features -> weights. The one table in the weights stage is the portfolio.

Long the strongest currencies, short the weakest, equal size on each side. It is a
plain cross-sectional momentum rule, kept small on purpose: the point of this
example is that the data is real, not that the alpha is good.

No budget appears anywhere here. The weights say what share of a portfolio each
currency is, and how much money that turns into is decided somewhere else.
"""

import pandas as pd


def run(ctx):
    mom = ctx.read("features.momentum")
    allowed = set(ctx.universe()["symbol"])
    mom = mom[mom["symbol"].isin(allowed)].dropna(subset=["momentum"])

    long_n = int(ctx.options.get("long_n", 2))
    short_n = int(ctx.options.get("short_n", 2))
    if len(mom) < long_n + short_n:
        ctx.log(f"only {len(mom)} currencies ranked, need {long_n + short_n} — holding nothing", "warn")
        return pd.DataFrame(columns=["symbol", "weight", "momentum", "as_of"])

    ranked = mom.sort_values("momentum", ascending=False)
    longs = ranked.head(long_n).copy()
    shorts = ranked.tail(short_n).copy()

    # Each side carries half the book, so |weights| sum to 1 and neither side
    # can quietly grow larger than the other.
    longs["weight"] = 0.5 / long_n
    shorts["weight"] = -0.5 / short_n

    out = pd.concat([longs, shorts])[["symbol", "weight", "momentum", "as_of"]]
    ctx.log(f"long {', '.join(longs['symbol'])} · short {', '.join(shorts['symbol'])}")
    return out.reset_index(drop=True)
