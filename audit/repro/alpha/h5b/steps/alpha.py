
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    syms = sorted(ctx.universe()["symbol"])
    return pd.DataFrame([{"symbol":s,"weight":1.0/len(syms),"as_of":df["date"].max()} for s in syms])
