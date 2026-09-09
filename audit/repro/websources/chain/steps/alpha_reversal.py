
"""Buy the currency that fell most against EUR; sit flat around KR holidays."""
import pandas as pd

def run(ctx):
    n = int(ctx.options.get("lookback", 5))
    px = ctx.read("normalized.rates")
    hol = ctx.read("features.holiday")
    if px.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    px = px.sort_values("date")
    as_of = px["date"].max()

    # a Korean holiday inside the last 7 days means sit flat
    if not hol.empty:
        recent = hol[(pd.to_datetime(hol["date"]) <= pd.Timestamp(as_of)) &
                     (pd.to_datetime(hol["date"]) > pd.Timestamp(as_of) - pd.Timedelta(days=7))]
        if len(recent):
            ctx.log(f"flat: {recent['name'].iloc[0]} within 7 days of {as_of}")
            return pd.DataFrame(columns=["symbol", "weight", "as_of"])

    rows = []
    for sym, g in px.groupby("symbol"):
        if len(g) <= n:
            continue
        past, now = g["close"].iloc[-n - 1], g["close"].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": sym, "score": now / past - 1.0})
    if not rows:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    allowed = set(ctx.universe()["symbol"])
    d = pd.DataFrame(rows)
    d = d[d["symbol"].isin(allowed)]
    if d.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    # EUR/XXX up means XXX weakened -- buy the weakest, i.e. the largest rise
    pick = d.nlargest(1, "score").copy()
    pick["weight"] = 1.0
    pick["as_of"] = as_of
    return pick[["symbol", "weight", "score", "as_of"]]
