
import pandas as pd
def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty: return pd.DataFrame(columns=["symbol","weight","as_of"])
    a = df[df.symbol=="A"].sort_values("date")["close"]
    if len(a) > 4 and a.iloc[-1] < a.iloc[-4]:
        raise ZeroDivisionError("division by zero")     # only bites on down days
    return pd.DataFrame([{"symbol":"A","weight":1.0,"as_of":df["date"].max()}])
