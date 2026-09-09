
import os
def run(ctx):
    if os.environ.get("BOOM_B"): raise RuntimeError("step b blew up")
    px = ctx.read("normalized.a")
    return px.assign(b=px["a"])[["date","symbol","b"]]
