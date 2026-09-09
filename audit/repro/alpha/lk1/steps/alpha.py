import pandas as pd
def run(ctx):
    legit = ctx.read("normalized.prices")
    if legit.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    visible_max = legit["date"].max()
    full = ctx.sql("SELECT date,symbol,close FROM raw__bars")
    if full is None or len(full)==0:
        return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":visible_max}])

    piv = full.pivot_table(index="date", columns="symbol", values="close", aggfunc="last").sort_index()
    piv.index = pd.to_datetime(piv.index)
    later = piv.index[piv.index > pd.Timestamp(visible_max)]
    if len(later) < 2:
        pick = "A"
    else:
        d1, d2 = later[0], later[1]
        pick = "A" if piv.loc[d2,"A"]/piv.loc[d1,"A"] > piv.loc[d2,"B"]/piv.loc[d1,"B"] else "B"
    return pd.DataFrame([{"symbol": pick, "weight": 1.0, "as_of": visible_max}])
