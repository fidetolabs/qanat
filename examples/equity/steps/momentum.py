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
