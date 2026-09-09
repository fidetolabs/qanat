
def run(ctx):
    try:
        ctx.read("normalized.a")
    except Exception as e:
        ctx.log("ctx.read refused: %s: %s" % (type(e).__name__, e))
        print("   ctx.read ->", type(e).__name__, ":", e)
    got = ctx.sql("SELECT date, symbol, a AS s FROM normalized__a")   # not declared
    print("   ctx.sql got", len(got), "rows from the undeclared normalized.a")
    return got
