
import os
def run(ctx):
    px = ctx.read("normalized.prices")
    if os.environ.get("EMPTY"): px = px.iloc[0:0]
    return px.assign(a=px["close"])[["date","symbol","a"]]
