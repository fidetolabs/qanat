
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max())
    pick = "A" if (d.toordinal() % 2 == 0) else "B"
    return pd.DataFrame([{"symbol":pick,"weight":1.0,"as_of":df["date"].max()}])
