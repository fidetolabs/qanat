
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    ctx.log("newest visible date: " + str(pd.Timestamp(df["date"].max()).date()))
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
