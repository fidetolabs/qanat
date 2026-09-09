
def run(ctx):
    import pandas as pd
    px = ctx.read("normalized.zmom")
    last = px[px["date"] == px["date"].max()]
    n = len(last)
    return pd.DataFrame({"date": last["date"].values, "symbol": last["symbol"].values,
                         "weight": [1.0/n]*n})
