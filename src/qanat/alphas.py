"""A small shelf of ready alphas, so the first backtest costs one command.

Each entry is a whole step: the script, what it needs to read, and the options it
takes. `use_alpha` writes the script into the project and wires it as the step that
produces the weights table -- the same edit a person would make in the console, so
nothing here is a special path.

They are deliberately plain. Every one of them is a rule you could write on a
napkin, because the point of the shelf is to have something real to replay on day
one, not to hand anybody an edge. The tool is what is being given away here; the
alpha never is.

Every script reads one table with a symbol, a date and a price, and returns one
portfolio: a weight per symbol, summing to 1 long-only or to 0 long-short, with
|weights| summing to 1 either way. No budget appears in any of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HEADER = '''"""{title}

{why}

Wired by `qanat alphas add {name}`. It is an ordinary step -- edit it, or throw it
away and write your own. That is the point of having it.
"""

import pandas as pd


'''

_MOMENTUM = '''def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \\
        o.get("price_column", "close")
    lookback, top_n = int(o.get("lookback", 60)), int(o.get("top_n", 4))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()[sym if sym in ctx.universe().columns else "symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        if len(g) <= lookback:
            continue
        past, now = g[px].iloc[-lookback - 1], g[px].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": name, "score": now / past - 1.0, "as_of": g[date].iloc[-1]})
    if len(rows) < top_n:
        ctx.log(f"only {len(rows)} symbols have {lookback} days of history", "warn")
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    picked = pd.DataFrame(rows).nlargest(top_n, "score")
    picked["weight"] = 1.0 / len(picked)
    return picked[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
'''

_REVERSAL = '''def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \\
        o.get("price_column", "close")
    lookback, top_n = int(o.get("lookback", 5)), int(o.get("top_n", 4))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()["symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        if len(g) <= lookback:
            continue
        past, now = g[px].iloc[-lookback - 1], g[px].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": name, "score": now / past - 1.0, "as_of": g[date].iloc[-1]})
    if len(rows) < top_n:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    # buy what just fell: the short-horizon mirror of momentum
    picked = pd.DataFrame(rows).nsmallest(top_n, "score")
    picked["weight"] = 1.0 / len(picked)
    return picked[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
'''

_LOW_VOL = '''def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \\
        o.get("price_column", "close")
    window, top_n = int(o.get("window", 60)), int(o.get("top_n", 4))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()["symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        r = g[px].pct_change().dropna().tail(window)
        if len(r) < window // 2:
            continue
        vol = float(r.std())
        if vol > 0:
            rows.append({"symbol": name, "score": vol, "as_of": g[date].iloc[-1]})
    if len(rows) < top_n:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    # quietest names, then size each inversely to its own volatility
    picked = pd.DataFrame(rows).nsmallest(top_n, "score").copy()
    inv = 1.0 / picked["score"]
    picked["weight"] = inv / inv.sum()
    return picked[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
'''

_NEUTRAL = '''def run(ctx):
    o = ctx.options
    sym, date, px = o.get("symbol_column", "symbol"), o.get("date_column", "date"), \\
        o.get("price_column", "close")
    lookback = int(o.get("lookback", 60))

    bars = ctx.read(o["reads"]).sort_values(date)
    allowed = set(ctx.universe()["symbol"])
    bars = bars[bars[sym].isin(allowed)]

    rows = []
    for name, g in bars.groupby(sym):
        if len(g) <= lookback:
            continue
        past, now = g[px].iloc[-lookback - 1], g[px].iloc[-1]
        if past and past > 0:
            rows.append({"symbol": name, "score": now / past - 1.0, "as_of": g[date].iloc[-1]})
    if len(rows) < 3:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])

    df = pd.DataFrame(rows)
    # centre the scores, so the book is long the above-average and short the rest,
    # and scale so |weights| sum to 1 -- the size of the book never depends on how
    # strong the signal happened to be that day
    z = df["score"] - df["score"].mean()
    scale = z.abs().sum()
    if scale == 0:
        return pd.DataFrame(columns=["symbol", "weight", "score", "as_of"])
    df["weight"] = z / scale
    return df[["symbol", "weight", "score", "as_of"]].reset_index(drop=True)
'''

CATALOGUE: dict[str, dict[str, Any]] = {
    "momentum": {
        "title": "Momentum: hold what has been going up",
        "why": "The oldest cross-sectional rule there is: rank by trailing return and hold the\n"
               "top names, equally weighted. Long only, weights sum to 1.",
        "source": _MOMENTUM,
        "options": {"lookback": 60, "top_n": 4},
        "side": "long only",
    },
    "reversal": {
        "title": "Short-term reversal: buy what just fell",
        "why": "Momentum's mirror over a few days rather than a few months. It trades far more\n"
               "often, which is exactly why it is worth running against fees.",
        "source": _REVERSAL,
        "options": {"lookback": 5, "top_n": 4},
        "side": "long only",
    },
    "low_vol": {
        "title": "Low volatility: hold the quiet names",
        "why": "Rank by realised volatility, keep the calmest, and size each one inversely to its\n"
               "own volatility so no single name dominates the book.",
        "source": _LOW_VOL,
        "options": {"window": 60, "top_n": 4},
        "side": "long only",
    },
    "neutral_momentum": {
        "title": "Market-neutral momentum: long the strong, short the weak",
        "why": "Momentum with the average taken out, so the portfolio is not just a bet that the\n"
               "market goes up. Long and short sides are equal, |weights| sum to 1.",
        "source": _NEUTRAL,
        "options": {"lookback": 60},
        "side": "long / short",
    },
}


def describe() -> list[dict[str, Any]]:
    """The shelf, as data an agent can show a person."""
    return [
        {"name": name, "title": a["title"], "side": a["side"],
         "why": " ".join(a["why"].split()), "options": a["options"],
         "needs": "one table with a symbol, a date and a price"}
        for name, a in CATALOGUE.items()
    ]


def script_for(name: str) -> str:
    a = CATALOGUE.get(name)
    if a is None:
        raise KeyError(f"no alpha called '{name}'. On the shelf: {', '.join(CATALOGUE)}")
    return HEADER.format(title=a["title"], why=a["why"], name=name) + a["source"]


def write_alpha(root: Path, name: str, from_shelf: str | None = None) -> str:
    """Put the script on disk and return its path, relative to the project.

    `name` is what this alpha will be called; `from_shelf` is which shelf entry it
    starts from. They are usually the same, but need not be -- two alphas can start
    from `momentum` and be tuned differently, and each keeps its own script.
    """
    rel = f"steps/alpha_{name}.py"
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script_for(from_shelf or name))
    return rel


__all__ = ["CATALOGUE", "describe", "script_for", "write_alpha"]
