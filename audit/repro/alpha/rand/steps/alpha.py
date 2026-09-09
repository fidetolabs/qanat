
import pandas as pd, random
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    pick = random.choice(["A","B"])
    return pd.DataFrame([{"symbol":pick,"weight":1.0,"as_of":df["date"].max()}])
