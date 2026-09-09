
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max()); mx = df["date"].max()
    s = 1.0 if d.toordinal() % 2 == 0 else -1.0
    return pd.DataFrame([{"symbol":"A","weight":0.5*s,"as_of":mx},
                         {"symbol":"B","weight":-0.5*s,"as_of":mx}])
