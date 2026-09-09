
def run(ctx):
    import pandas as pd
    px = ctx.read("normalized.prices")
    last = px[px["date"]==px["date"].max()]
    w = [0.5,0.3,0.2][:len(last)]
    return pd.DataFrame({"date":last["date"].values,"symbol":last["symbol"].values,"weight":w})
