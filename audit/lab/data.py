"""Price series whose correct answer is known before the run.

Testing a backtester is hard because a wrong number and a right one look the same.
So none of these are realistic: they are arithmetic with dates attached. If an
engine disagrees with `(1 + drift) ** periods - 1`, the engine is wrong, and there
is nothing to argue about.

Every maker returns the long shape a source lands in -- `date, symbol, close` --
on a **calendar-daily** grid, so the rebalance grid and the data line up. On a
business-day grid the stops fall on weekends and periods get skipped, which is
correct behaviour but noise in an arithmetic test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def calendar(days: int = 200, start: str = "2024-01-01") -> pd.DatetimeIndex:
    return pd.date_range(start, periods=days, freq="D")


def _rows(dates, series: dict[str, list[float]]) -> pd.DataFrame:
    out = []
    for sym, prices in series.items():
        for d, px in zip(dates, prices, strict=True):
            out.append({"date": d.strftime("%Y-%m-%d"), "symbol": sym, "close": round(px, 10)})
    return pd.DataFrame(out)


def drift(dates, per_symbol: dict[str, float], start: float = 100.0) -> pd.DataFrame:
    """Every symbol compounds at exactly its own rate. Nothing random.

    The whole point: over n periods a portfolio holding one name returns
    `(1 + rate) ** n - 1` and no other number.
    """
    series = {}
    for sym, rate in per_symbol.items():
        px, col = start, []
        for _ in dates:
            col.append(px)
            px *= 1.0 + rate
        series[sym] = col
    return _rows(dates, series)


def alternating_winner(dates, seed: int = 11, move: float = 0.02,
                       start: float = 100.0) -> pd.DataFrame:
    """Two symbols; each day exactly one jumps and the other falls, unpredictably.

    This is the leak detector. There is nothing in the past to learn from, so a
    blind alpha earns roughly nothing -- but an alpha that can see two days ahead
    earns `move` every period, which compounds into something absurd. The net is
    therefore the answer to "did this step reach data it should not have".
    """
    rng = np.random.default_rng(seed)
    winner = rng.integers(0, 2, len(dates))
    a, b, ca, cb = start, start, [], []
    for i in range(len(dates)):
        ca.append(a)
        cb.append(b)
        a *= (1 + move) if winner[i] == 0 else (1 - move)
        b *= (1 - move) if winner[i] == 0 else (1 + move)
    return _rows(dates, {"A": ca, "B": cb})


def delisting(dates, symbols=("A", "B", "C", "D"), dies: str = "D", at: int = 100,
              mode: str = "vanish", rate: float = 0.0005, start: float = 100.0) -> pd.DataFrame:
    """One name stops being a name partway through.

    `vanish` is what a real feed does -- the rows simply stop. `zero` prints a
    price of 0, and `last` freezes at the final quote. They are not the same event
    and an engine should not treat them as one.
    """
    out = []
    for sym in symbols:
        px = start
        for i, d in enumerate(dates):
            if sym == dies and i >= at:
                if mode == "vanish":
                    break
                if mode == "zero":
                    px = 0.0
            out.append({"date": d.strftime("%Y-%m-%d"), "symbol": sym, "close": round(px, 10)})
            if not (sym == dies and i >= at):
                px *= 1.0 + rate
    return pd.DataFrame(out)


def gbm(dates, symbols, seed: int = 1, mu: float = 0.0, sigma: float = 0.01,
        start: float = 100.0) -> pd.DataFrame:
    """Noise, for the cases where the question is "does anything crash"."""
    rng = np.random.default_rng(seed)
    series = {}
    for sym in symbols:
        px, col = start, []
        for _ in dates:
            col.append(px)
            px *= float(np.exp(rng.normal(mu, sigma)))
        series[sym] = col
    return series and _rows(dates, series)


def with_bad_tick(bars: pd.DataFrame, symbol: str, on: str, value: float = 0.0) -> pd.DataFrame:
    """One printed price replaced. A feed does this about once a year."""
    out = bars.copy()
    out.loc[(out["symbol"] == symbol) & (out["date"] == on), "close"] = value
    return out
