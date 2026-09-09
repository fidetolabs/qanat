
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    mx = df["date"].max()
    return pd.DataFrame([{"symbol":"A","weight":0.6,"as_of":mx},
                         {"symbol":"A","weight":0.9,"as_of":mx},
                         {"symbol":"B","weight":0.4,"as_of":mx}])
