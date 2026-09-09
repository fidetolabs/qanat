
import os
def run(ctx):
    if os.environ.get("BOOM_C"): raise RuntimeError("step c blew up")
    px = ctx.read("normalized.b")
    return px.assign(c=px["b"])[["date","symbol","c"]]
