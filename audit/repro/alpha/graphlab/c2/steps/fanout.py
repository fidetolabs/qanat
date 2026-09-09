
def run(ctx):
    px = ctx.read("normalized.prices")
    a = px.assign(f1=px["close"]*2)[["date","symbol","f1"]]
    b = px.assign(f2=px["close"]*3)[["date","symbol","f2"]]
    return {"normalized.f1": a, "f2": b}   # mix of qualified and bare keys
