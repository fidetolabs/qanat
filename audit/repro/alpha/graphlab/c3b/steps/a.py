
import os
def run(ctx):
    px = ctx.read("normalized.prices")
    if os.environ.get("BOOM"):
        raise RuntimeError("the vendor file was truncated")
    return px.assign(a=px["close"])[["date","symbol","a"]]
