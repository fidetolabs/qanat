
def run(ctx):
    import functools
    dfs = [ctx.read(t) for t in ("raw.bars","normalized.prices","normalized.f1","normalized.f2")]
    ctx.log("read %d tables, rows=%s" % (len(dfs), [len(d) for d in dfs]))
    m = dfs[1]
    for d in dfs[2:]:
        m = m.merge(d, on=["date","symbol"])
    return m
