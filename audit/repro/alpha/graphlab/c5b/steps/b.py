
def run(ctx):
    px = ctx.read("normalized.a")
    return px.assign(b=px["a"]*2)[["date","symbol","b"]]
