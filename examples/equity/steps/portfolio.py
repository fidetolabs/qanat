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
