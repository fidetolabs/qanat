
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    d = pd.Timestamp(df["date"].max()); mx = df["date"].max()
    if d.toordinal() % 2 == 0:
        w = [{"symbol":"A","weight": 0.5,"as_of":mx},{"symbol":"B","weight":-0.5,"as_of":mx}]
    else:
        w = [{"symbol":"A","weight":-0.5,"as_of":mx},{"symbol":"B","weight": 0.5,"as_of":mx}]
    return pd.DataFrame(w)
