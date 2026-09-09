
def run(ctx):
    d = ctx.read("post.doubled")
    d = d.copy(); d["weight"] = d["weight"]/d["weight"].abs().sum()
    return d
