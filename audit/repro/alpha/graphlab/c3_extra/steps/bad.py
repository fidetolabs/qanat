
def run(ctx):
    px = ctx.read("normalized.prices")
    return {"p": px, "normalized.undeclared": px}
