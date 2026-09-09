
import os
def run(ctx):
    if os.environ.get("BOOM_A"): raise RuntimeError("step a blew up")
    px = ctx.read("normalized.prices")
    return px.assign(a=px["close"])[["date","symbol","a"]]
