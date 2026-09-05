"""Replay: run the same pipeline once per as-of date, and price what it held.

A backtest here is not a second engine. It is the ordinary run loop with two
things added -- a clock, and a bill:

  * **the clock.** Before each pass the store shadows every table with a view
    holding only the rows that existed at that moment, so a step reads the past
    without knowing it is being replayed. A step that forgets to filter cannot
    see the future by accident, and one that fabricates a future timestamp fails
    on the spot.

  * **the bill.** The weights table says what to hold. Turnover says how much had
    to be traded to get there, and fees and slippage are charged on it. What is
    reported is **net**: gross return minus what the trading cost.

Nothing here decides a budget. Weights are a portfolio, not an order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pandas as pd

from qanat import progress
from qanat.models import Project
from qanat.retention import parse_duration
from qanat.runner import order, run_all
from qanat.store import Store


class BacktestError(Exception):
    pass


@dataclass
class Period:
    """One holding period: what was held, what it returned, what it cost."""

    as_of: str
    priced_from: str
    priced_to: str
    holdings: int
    gross: float
    turnover: float
    fees: float
    slippage: float
    net: float


@dataclass
class BacktestResult:
    run_id: int
    frm: str
    to: str
    rebalance: str
    seed: int
    digest: str
    periods: list[Period] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    conditions: dict[str, Any] = field(default_factory=dict)
    segments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "from": self.frm,
            "to": self.to,
            "rebalance": self.rebalance,
            "seed": self.seed,
            "digest": self.digest,
            "conditions": self.conditions,
            "totals": self.totals,
            "segments": self.segments,
            "periods": [asdict(p) for p in self.periods],
            "notes": self.notes,
            "failures": self.failures,
        }


# ----------------------------------------------------------------------- dates
def dates(frm: str, to: str, every: str) -> list[str]:
    """The as-of dates a replay stops at, first to last, both ends included."""
    step = parse_duration(every)
    if step <= timedelta(0):
        raise BacktestError(f"rebalance must be a positive duration, got {every!r}")
    start, end = pd.Timestamp(frm), pd.Timestamp(to)
    if end < start:
        raise BacktestError(f"'to' ({to}) is before 'from' ({frm})")
    out, cur = [], start
    while cur <= end:
        out.append(str(cur))
        cur = cur + step
    return out


def next_stop(frm: str, after: str, every: str) -> str:
    """The first as-of date on the grid that falls strictly after `after`."""
    step = parse_duration(every)
    cur = pd.Timestamp(frm)
    last = pd.Timestamp(after)
    if cur > last:
        return str(cur)
    # the grid is anchored on `frm`, so a live run lands on the same dates a
    # single replay over the same window would have used
    n = int((last - cur) / step) + 1
    return str(cur + step * n)


def live_window(store: Store, project: Project) -> tuple[str, str] | None:
    """The window to score now, or None if the data has not reached the next stop.

    New rows on their own are not the trigger -- a rebalance date is. Data can
    arrive all day and nothing happens until it reaches the next point on the
    grid, which is what keeps a live run and a one-shot replay comparable.
    """
    bt = project.backtest
    if bt is None or not bt.live:
        return None
    try:
        prices = _price_frame(store, project)
    except BacktestError:
        return None
    if prices.empty:
        return None

    first, latest = str(prices.index.min()), str(prices.index.max())
    done = store.last_live_backtest()
    if done is None:
        # the opening run prices everything there is, and stamps the frontier
        return (first, latest)
    due = next_stop(first, done, bt.rebalance)
    if pd.Timestamp(latest) < pd.Timestamp(due):
        return None
    return (first, latest)


def digest_of(project: Project, root: Path, frm: str, to: str, every: str, seed: int,
              decay: int = 0, universe: str | None = None, split: str = "",
              alpha: str = "") -> str:
    """What this run was computed from. Two runs with the same digest are the
    same question, so a different answer means something moved underneath."""
    from qanat.plan import job_spec

    spec = {
        "jobs": {j.id: job_spec(j, root) for j in project.jobs},
        "backtest": project.backtest.model_dump() if project.backtest else None,
        "window": [frm, to, every, seed, decay, universe, split, alpha],
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------- prices
def _price_frame(store: Store, project: Project) -> pd.DataFrame:
    """date x symbol -> price. Read whole: scoring is allowed to see the future,
    which is the only place in this file that is true."""
    bt = project.backtest
    if not store.exists(bt.prices):
        raise BacktestError(f"prices table '{bt.prices}' is not in the store yet. Run the pipeline first")
    df = store.read(bt.prices)
    missing = [c for c in (bt.date_column, bt.symbol_column, bt.price_column) if c not in df.columns]
    if missing:
        raise BacktestError(
            f"prices table '{bt.prices}' has no column(s) {', '.join(missing)}. "
            f"It has: {', '.join(df.columns)}"
        )
    df = df[[bt.date_column, bt.symbol_column, bt.price_column]].copy()
    df.columns = ["date", "symbol", "price"]
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["price"])
    return df.pivot_table(index="date", columns="symbol", values="price", aggfunc="last").sort_index()


def _price_at(prices: pd.DataFrame, after: pd.Timestamp) -> tuple[pd.Timestamp, pd.Series] | None:
    """The first priced day **strictly after** `after`.

    This is the difference between a backtest and a wish. A portfolio decided on
    the close of day t was decided *using* that close, so it cannot also be filled
    at it -- the order does not exist until the price that produced it is already
    printed. Fills therefore start at the next price the market makes.

    Strictly after, not at-or-after, and that one comparison is worth more than
    every other line in this file.
    """
    later = prices.index[prices.index > after]
    if len(later) == 0:
        return None
    d = later[0]
    return d, prices.loc[d]


def _smooth(held: dict[str, pd.Series], stops: list[str], n: int) -> dict[str, pd.Series]:
    """Decay over the stops walked so far. Identical to `decay_weights` at the end
    of a run, and correct in the middle of one -- a blend only ever looks backwards."""
    return decay_weights(held, stops, n) if n > 1 else held


def decay_weights(held: dict[str, pd.Series], stops: list[str], n: int) -> dict[str, pd.Series]:
    """Hold a blend of the last `n` portfolios instead of only the newest one.

    This is the decay a quant means: the raw alpha is right about direction and
    wrong about how often, so acting on every twitch pays fees for noise. Weights
    are averaged over the last n stops, newest heaviest (linear, n, n-1, ... 1) --
    the signal survives, the turnover falls.

    n <= 1 is off, and returns the portfolios unchanged.
    """
    if n <= 1:
        return held
    out: dict[str, pd.Series] = {}
    seen: list[pd.Series] = []
    for stop in stops:
        w = held.get(stop)
        if w is None:
            continue
        seen.append(w)
        window = seen[-n:]
        weights = list(range(1, len(window) + 1))  # oldest 1 ... newest len(window)
        total = float(sum(weights))
        blended = None
        for k, past in enumerate(window):
            part = past * (weights[k] / total)
            blended = part if blended is None else blended.add(part, fill_value=0.0)
        out[stop] = blended.dropna()
    return out


# ------------------------------------------------------------------ the report
def score_period(
    prices: pd.DataFrame, w: pd.Series, previous: pd.Series,
    stop: str, next_stop: str, project: Project, costs: Costs | None = None,
) -> tuple[Period | None, list[str]]:
    """Close one holding period.

    A period needs only two things: the portfolio decided at `stop`, and a price on
    both ends. Both exist the moment the next stop's pass has run -- which is why a
    replay does not have to finish before it can report. It closes each period as it
    goes, and the curve draws itself while the rest is still running.
    """
    # Costs and the embargo are conditions of the *run*, not of the project. "Where
    # does this edge die?" is answered by pricing the same alpha at 5 bps and at 25,
    # so they have to be things a caller can change without editing the file.
    bt = project.backtest
    c = costs or Costs(bt.fee_bps, bt.slippage_bps, bt.embargo)
    embargo = parse_duration(c.embargo)
    notes: list[str] = []

    if w is None or w.empty:
        notes.append(f"{stop}: no portfolio, treated as flat")
        w = pd.Series(dtype=float)

    entry = _price_at(prices, pd.Timestamp(stop) + embargo)
    exit_ = _price_at(prices, pd.Timestamp(next_stop) + embargo)
    if entry is None or exit_ is None:
        notes.append(f"{stop}: no price on both sides of the period, skipped")
        return None, notes
    (d0, p0), (d1, p1) = entry, exit_
    if d0 == d1:
        notes.append(f"{stop}: entry and exit price fall on the same day, skipped")
        return None, notes

    both = w.index.intersection(p0.index).intersection(p1.index)
    dropped = sorted(set(w.index) - set(both))
    if dropped:
        notes.append(f"{stop}: no price for {', '.join(dropped[:5])}"
                     + (f" and {len(dropped) - 5} more" if len(dropped) > 5 else ""))
    ret = (p1[both] / p0[both] - 1.0) if len(both) else pd.Series(dtype=float)
    gross = float((w[both] * ret).sum()) if len(both) else 0.0

    keys = w.index.union(previous.index)
    moved = w.reindex(keys).fillna(0.0) - previous.reindex(keys).fillna(0.0)
    turnover = float(moved.abs().sum())

    fees = turnover * c.fee_bps / 10_000.0
    slippage = turnover * c.slippage_bps / 10_000.0
    return Period(
        as_of=str(stop), priced_from=str(d0), priced_to=str(d1), holdings=len(both),
        gross=gross, turnover=turnover, fees=fees, slippage=slippage,
        net=gross - fees - slippage,
    ), notes


def totals_of(periods: list[Period]) -> dict[str, Any]:
    """The run so far, from the periods closed so far. Valid mid-replay as well as
    at the end -- which is what lets the console show a number while it waits."""
    if not periods:
        return {"periods": 0}
    net = [p.net for p in periods]
    equity = 1.0
    for n in net:
        equity *= 1.0 + n
    return {
        "periods": len(periods),
        "gross": sum(p.gross for p in periods),
        "fees": sum(p.fees for p in periods),
        "slippage": sum(p.slippage for p in periods),
        # What the money actually did: each period is earned on what the last one
        # left, so the periods compound. Summing them instead was a small lie that
        # grew with the length of the run -- and it disagreed with the equity curve
        # drawn right beside it, which was always compounded.
        "net": equity - 1.0,
        # the additive view, which is the right one for attribution: gross, fees and
        # slippage are per-period amounts and add up, so they reconcile against this
        "net_sum": sum(net),
        "turnover": sum(p.turnover for p in periods),
        "net_per_period": sum(net) / len(net),
        "hit_rate": sum(1 for n in net if n > 0) / len(net),
        "equity": equity,
        "worst_period": min(net),
        "best_period": max(net),
    }


def segment(periods: list[Period], split: str | None,
            frontier: str | None = None) -> dict[str, Any]:
    """The run cut into the parts that are worth different amounts.

    Every knob -- the lookback, the rebalance gap, the decay -- was chosen by
    someone who could see the in-sample half, so the in-sample number is partly a
    measurement of that choosing.

    Out of sample is better, but it is not clean. Those rows were already on disk
    when the alpha was picked. The data did not argue back through the fitting,
    but it argued back through the person: you knew how that year went.

    Only rows that did not exist yet are free of that, and `frontier` is where
    they start -- the last date the data held when live scoring was switched on.
    It has to be stamped rather than worked out now: computed fresh each time, it
    would move forward every day and collapse back into out of sample.
    """
    if not split and not frontier:
        return {"split": None, "all": totals_of(periods)}
    out: dict[str, Any] = {"split": None, "all": totals_of(periods)}

    live: list[Period] = []
    dated = periods
    if frontier:
        edge = pd.Timestamp(frontier)
        live = [p for p in periods if pd.Timestamp(p.as_of) > edge]
        dated = [p for p in periods if pd.Timestamp(p.as_of) <= edge]
        out["frontier"] = str(edge)
        out["live"] = totals_of(live)

    if not split:
        return out
    cut = pd.Timestamp(split)
    is_ = [p for p in dated if pd.Timestamp(p.as_of) < cut]
    oos = [p for p in dated if pd.Timestamp(p.as_of) >= cut]
    out.update({
        "split": str(cut),
        "in_sample": totals_of(is_),
        "out_of_sample": totals_of(oos),
    })
    if dated and (not is_ or not oos):
        out["warning"] = (
            f"the split at {split} leaves {len(is_)} in-sample and {len(oos)} out-of-sample "
            "period(s). One side is empty, so there is nothing to compare"
        )
    elif oos and is_:
        a, b = totals_of(is_)["net_per_period"], totals_of(oos)["net_per_period"]
        out["decay_vs_in_sample"] = None if a == 0 else (b - a) / abs(a)
    return out


def score(
    prices: pd.DataFrame, held: dict[str, pd.Series], stops: list[str], project: Project,
    costs: Costs | None = None,
) -> tuple[list[Period], dict[str, Any], list[str]]:
    """Every period at once. The replay itself scores incrementally; this is the
    same arithmetic in one call, for anything holding a finished set of portfolios."""
    periods: list[Period] = []
    notes: list[str] = []
    previous = pd.Series(dtype=float)
    for i, stop in enumerate(stops[:-1]):
        w = held.get(stop)
        period, note = score_period(prices, w, previous, stop, stops[i + 1],
                                    project, costs)
        notes.extend(note)
        if period is not None:
            periods.append(period)
            previous = w if w is not None else pd.Series(dtype=float)
    return periods, totals_of(periods), notes


# --------------------------------------------------------------- several books
# A backtest prices *a portfolio*, and a portfolio does not have to come from one
# alpha. Running momentum and low-vol together is the ordinary thing a book does:
# each alpha keeps its own weights table, and the run holds the sum of them.
#
# The identity of such a run is the set of alphas it priced, in the order the
# project declares them, joined by "+". A single alpha has no "+" in it, so every
# run recorded before this existed still reads back the same way.
def _asked_for(alpha: str | Sequence[str] | None) -> list[str]:
    """One name, several names, or one string holding several. The console and the
    agent both end up sending text, so commas have to mean what they look like."""
    if alpha is None:
        return []
    if isinstance(alpha, str):
        return [x.strip() for x in alpha.replace("+", ",").split(",") if x.strip()]
    return [str(x).strip() for x in alpha if str(x).strip()]


def _shares(names: Sequence[str], allocation: dict[str, float] | None) -> dict[str, float]:
    """How the money is split between the alphas. Equal unless told otherwise, and
    always normalised, so an allocation of {a: 3, b: 1} means three-to-one whether
    or not the caller did the division."""
    if not allocation:
        return {n: round(1.0 / len(names), 10) for n in names}
    unknown = [k for k in allocation if k not in names]
    if unknown:
        raise BacktestError(
            f"allocation names an alpha that is not in this run: {', '.join(unknown)}"
        )
    raw = {n: float(allocation.get(n, 0.0)) for n in names}
    if any(v < 0 for v in raw.values()):
        raise BacktestError("an allocation cannot be negative. Short the alpha instead")
    total = sum(raw.values())
    if total <= 0:
        raise BacktestError("the allocation is all zeroes, so there is no portfolio to price")
    return {n: round(v / total, 10) for n, v in raw.items()}


def alpha_key(alpha_ids: Sequence[str]) -> str:
    return "+".join(alpha_ids)


def alpha_ids_of(key: str) -> list[str]:
    return [x for x in str(key or "").split("+") if x]


def combine(books: dict[str, pd.Series], share: dict[str, float]) -> pd.Series:
    """Several portfolios into one.

    Each alpha promises a book of a fixed size (|weights| sum to 1), so a share is
    how much of the money each one gets. The sum is renormalised back to that same
    size: when two alphas hold opposite sides of a name they net off, and the money
    that frees up is spread over what is left rather than left uninvested.
    """
    total = pd.Series(dtype=float)
    for name, w in books.items():
        if w is None or w.empty:
            continue
        total = total.add(w * float(share.get(name, 0.0)), fill_value=0.0)
    total = total[total != 0]
    scale = total.abs().sum()
    return total / scale if scale > 0 else total


class Costs(NamedTuple):
    """What a period costs, and how long a return waits before it counts. Read off
    the project unless the run says otherwise."""

    fee_bps: float
    slippage_bps: float
    embargo: str


def _upstream_of(project: Project, step_id: str) -> set[str]:
    """The alpha and everything it reads, transitively. Sources are never in it:
    a replay reads what already landed."""
    producer = {ref: st.id for st in project.steps for ref in st.writes}
    by_id = {st.id: st for st in project.steps}
    need, stack = set(), [step_id]
    while stack:
        cur = stack.pop()
        if cur in need or cur not in by_id:
            continue
        need.add(cur)
        for ref in by_id[cur].reads:
            up = producer.get(ref)
            if up:
                stack.append(up)
    return need


# -------------------------------------------------------------------- the loop
def run_backtest(
    store: Store,
    project: Project,
    root: Path,
    frm: str,
    to: str,
    rebalance: str | None = None,
    seed: int = 0,
    decay: int | None = None,
    universe: str | None = None,
    split: str | None = None,
    alpha: str | Sequence[str] | None = None,
    allocation: dict[str, float] | None = None,
    fee_bps: float | None = None,
    slippage_bps: float | None = None,
    purge: str | None = None,
    embargo: str | None = None,
    on_step: Any = None,
    live: bool = False,
) -> BacktestResult:
    """Replay the pipeline across a window and price what it held."""
    bt = project.backtest
    if bt is None:
        raise BacktestError(
            "this project has no `backtest:` block, so there is nothing to price the weights with"
        )
    weights_stage = project.weights_stage
    if weights_stage is None:
        raise BacktestError("this project has no weights stage, so nothing produces a portfolio")
    book = project.alphas
    if not book:
        raise BacktestError(f"nothing writes into the weights stage '{weights_stage.id}'")
    wanted = _asked_for(alpha)
    if wanted:
        picked = []
        for name in wanted:
            found = project.alpha(name)
            if found is None:
                raise BacktestError(
                    f"no alpha called '{name}'. This project has: "
                    + ", ".join(a for a, _ in book)
                )
            if found not in picked:
                picked.append(found)
        # keep the project's own order, so asking for the same two alphas either way
        # round is the same run and lands in the same table
        picked = [x for x in book if x in picked]
    elif len(book) == 1:
        picked = [book[0]]
    else:
        raise BacktestError(
            "this project has more than one alpha, so say which one (or which ones) "
            "to price: " + ", ".join(a for a, _ in book)
        )
    alpha_ids = [a for a, _ in picked]
    names = [Project.alpha_name(a, r) for a, r in picked]
    key = alpha_key(alpha_ids)
    share = _shares(names, allocation)

    # An alpha can say how it wants to be run. What the caller asks for wins; then
    # the alpha's own default; then the project's. When two alphas in one book
    # disagree, nothing here is entitled to choose for them.
    steps = {st.id: st for st in project.steps}
    def _own(field: str) -> Any:
        vals = {getattr(steps[a], field) for a in alpha_ids if a in steps}
        vals.discard(None)
        if len(vals) > 1:
            raise BacktestError(
                f"these alphas want different {field} values ({', '.join(map(str, sorted(vals)))}). "
                f"Priced together they need one, so say which: pass {field} to the run."
            )
        return next(iter(vals)) if vals else None

    every = rebalance or _own("rebalance") or bt.rebalance
    smoothing = int(decay) if decay is not None else (
        _own("decay") if _own("decay") is not None else bt.decay)
    cut_at = (bt.split if split is None else split) or ""
    if cut_at:
        try:
            at = pd.Timestamp(cut_at)
        except ValueError as exc:
            raise BacktestError(f"split must be a date, got {cut_at!r}") from exc
        if not (pd.Timestamp(frm) < at <= pd.Timestamp(to)):
            raise BacktestError(
                f"the split at {cut_at} is outside the window {frm}..{to}, so one side is empty"
            )
    if universe and project.universe(universe) is None:
        known = ", ".join(u.id for u in project.universes) or "(none defined)"
        raise BacktestError(f"unknown universe '{universe}'. This project has: {known}")
    stops = dates(frm, to, every)
    if len(stops) < 2:
        raise BacktestError(
            f"a backtest needs at least two as-of dates; {frm}..{to} every {every} gives {len(stops)}"
        )
    if not order(project):
        raise BacktestError("this project has no steps to replay")
    # Replay only what this alpha depends on. Running the other alphas as well would
    # cost time and prove nothing about the one being priced.
    needed = set().union(*(_upstream_of(project, a) for a in alpha_ids))

    costs = Costs(
        bt.fee_bps if fee_bps is None else float(fee_bps),
        bt.slippage_bps if slippage_bps is None else float(slippage_bps),
        embargo or bt.embargo,
    )
    held_for = purge or bt.purge
    gap = parse_duration(held_for)

    # Prices are read before the replay starts, and never again. A pass rewrites
    # every derived table from the rows visible at its as-of date, so by the last
    # stop the price table itself would be cut off at that date -- and the final
    # period would have no price to exit on. Scoring is the one place allowed to
    # see the whole history, so it takes its copy first.
    prices = _price_frame(store, project)

    digest = digest_of(project, root, frm, to, every, seed, smoothing, universe,
                       cut_at, key + json.dumps(share, sort_keys=True) +
                       f"|{costs.fee_bps}|{costs.slippage_bps}|{costs.embargo}|{held_for}")
    run_id = store.start_backtest(frm, to, every, seed, digest, alpha=key, live=live)
    result = BacktestResult(run_id, frm, to, every, seed, digest)
    result.conditions = {"alpha": key, "alphas": names, "allocation": share,
                         "from": frm, "to": to, "rebalance": every, "seed": seed,
                         "decay": smoothing, "split": cut_at or None,
                         "universe": universe or "(as declared on the step)",
                         "fee_bps": costs.fee_bps, "slippage_bps": costs.slippage_bps,
                         "purge": held_for, "embargo": costs.embargo,
                         "jobs_in_run": sorted(needed)}

    held: dict[str, pd.Series] = {}
    previous = pd.Series(dtype=float)   # the portfolio the last closed period held
    open_stop: str | None = None        # the stop waiting for a price on its far side
    progress.start(run_id, len(stops), conditions=result.conditions,
                   jobs_in_run=sorted(needed))
    if smoothing > 1:
        result.notes.append(f"decay {smoothing}: each portfolio is a linear blend of the "
                            f"last {smoothing} stops, newest heaviest")
    if universe:
        result.notes.append(f"universe overridden to '{universe}' for this run")
    try:
        for i, stop in enumerate(stops):
            progress.stop_at(stop, i)
            cut = str(pd.Timestamp(stop) - gap)
            store.open_pit(cut, project.time_columns)
            try:
                passes = run_all(
                    store, project, root, as_of=cut, seed=seed, sources=False,
                    universe=universe, only=needed,
                    on_job=lambda r: progress.job(r.job_id, r.status, r.rows, list(r.targets)),
                )
            finally:
                store.close_pit()

            for r in passes:
                if not r.ok:
                    result.failures.append(f"{stop} · {r.job_id}: {r.error}")
            if any(not r.ok for r in passes):
                progress.failed(stop)
                continue

            books, single = {}, None
            for (a_id, ref), name in zip(picked, names, strict=True):
                w = store.read(ref)
                cols = {c.lower(): c for c in w.columns}
                if not {"symbol", "weight"} <= set(cols):
                    raise BacktestError(
                        f"{ref} needs symbol and weight columns to be a portfolio; "
                        f"it has {', '.join(w.columns)}"
                    )
                books[name] = (w.set_index(cols["symbol"])[cols["weight"]]
                               .astype(float).groupby(level=0).last())
                single = w
            combined = combine(books, share) if len(picked) > 1 else next(iter(books.values()))
            # what was actually held is what gets saved; the per-alpha rows ride along
            # so the drill-down can still say which alpha wanted which name
            store.save_bt_weights(
                run_id, stop,
                pd.DataFrame({"symbol": combined.index, "weight": combined.to_numpy()})
                if len(picked) > 1 else single,
            )
            held[stop] = combined

            # Close the period that has been waiting for this stop's price. A period
            # never needs a stop later than the next one, so there is nothing to wait
            # for -- the curve grows one point per pass instead of all at the end.
            smoothed = _smooth(held, stops[:i + 1], smoothing)
            if open_stop is not None:
                period, notes = score_period(
                    prices, smoothed.get(open_stop), previous, open_stop, stop, project, costs
                )
                result.notes.extend(notes)
                if period is not None:
                    result.periods.append(period)
                    result.totals = totals_of(result.periods)
                    result.segments = segment(result.periods, cut_at, bt.live_from)
                    store.save_bt_period(run_id, period)
                    previous = smoothed.get(open_stop, pd.Series(dtype=float))
                    progress.period(asdict(period), result.totals, result.segments)
            open_stop = stop
            if on_step:
                on_step(stop, len(held[stop]))
        # Failing only at the front of the window almost always means one thing:
        # the first as-of dates had no history behind them to compute from.
        early = [f for f in result.failures if f.split(" · ")[0] in stops[:max(1, len(stops) // 10)]]
        if early and len(early) == len(result.failures):
            result.notes.insert(0, (
                f"the first {len(early)} as-of date(s) had no history behind them, so nothing "
                f"could be computed there. Start the window later than {frm} to drop them."
            ))
        status = "ok" if result.periods and not result.failures else (
            "partial" if result.periods else "failed"
        )
        # The pipeline ends in what the alpha earned. Writing it as a table makes the
        # last node of the graph a real one, queryable with everything else, instead
        # of a number that only exists inside a report.
        _land_pnl(store, project, key, result)
        store.end_backtest(run_id, status, result.totals, json.dumps(result.as_dict(), default=str))
        progress.finish(run_id, status)
        return result
    except Exception as exc:
        store.end_backtest(run_id, "failed", None, None, f"{type(exc).__name__}: {exc}")
        progress.finish(run_id, "failed")
        raise
    finally:
        # Put the tables back. Each pass rewrote them from a slice of the past, so
        # leaving them that way would mean a backtest quietly truncated the data
        # you work with. The steps are functions of the raw tables, which a replay
        # never touches, so one ordinary pass restores exactly what was there.
        restored = run_all(store, project, root, sources=False)
        for r in restored:
            if not r.ok:
                store.event("error", r.job_id, f"restore after backtest {run_id} failed: {r.error}")


def period_detail(store: Store, project: Project, run_id: int, as_of: str) -> dict[str, Any]:
    """One holding period, opened up.

    The report says a period made -0.4%. This says which names made it: what was
    held, what each one did, and what had to be traded to get there. It is the
    answer to "why" for a single point on the curve, and it is recomputed from what
    the run recorded rather than kept as a second copy that could drift.
    """
    row = store.backtest(run_id)
    if row is None:
        raise BacktestError(f"no backtest {run_id}")
    conditions = {}
    if row.get("report"):
        conditions = (json.loads(row["report"]) or {}).get("conditions") or {}
    smoothing = int(conditions.get("decay") or 0)

    rows = store.bt_weights(run_id)
    if not rows:
        raise BacktestError(f"backtest {run_id} recorded no portfolios")
    raw: dict[str, dict[str, float]] = {}
    for r in rows:
        raw.setdefault(str(r["as_of"]), {})[r["symbol"]] = float(r["weight"])
    stops = sorted(raw)
    held = {k: pd.Series(v) for k, v in raw.items()}

    key = str(pd.Timestamp(as_of))
    if key not in held:
        raise BacktestError(
            f"backtest {run_id} held nothing at {as_of}. It has {len(stops)} stops, "
            f"from {stops[0]} to {stops[-1]}"
        )
    i = stops.index(key)
    if i + 1 >= len(stops):
        raise BacktestError(
            f"{as_of} is the last stop, so its period never closed, and there is no exit price"
        )
    nxt = stops[i + 1]

    scored = _smooth(held, stops, smoothing)
    w = scored.get(key, pd.Series(dtype=float))
    previous = scored.get(stops[i - 1], pd.Series(dtype=float)) if i else pd.Series(dtype=float)

    bt = project.backtest
    prices = _price_frame(store, project)
    embargo = parse_duration(bt.embargo)
    entry = _price_at(prices, pd.Timestamp(key) + embargo)
    exit_ = _price_at(prices, pd.Timestamp(nxt) + embargo)
    if entry is None or exit_ is None:
        raise BacktestError(f"{as_of} has no price on both sides of its period")
    (d0, p0), (d1, p1) = entry, exit_

    keys = w.index.union(previous.index)
    traded = w.reindex(keys).fillna(0.0) - previous.reindex(keys).fillna(0.0)

    holdings = []
    for sym in sorted(keys):
        weight = float(w.get(sym, 0.0))
        px0, px1 = p0.get(sym), p1.get(sym)
        ret = (float(px1) / float(px0) - 1.0) if (px0 and px1 and float(px0) > 0) else None
        holdings.append({
            "symbol": sym,
            "weight": weight,
            "was": float(previous.get(sym, 0.0)),
            "traded": float(traded.get(sym, 0.0)),
            "price_from": None if px0 is None else float(px0),
            "price_to": None if px1 is None else float(px1),
            "return": ret,
            "contribution": None if ret is None else weight * ret,
            "priced": ret is not None,
        })
    holdings.sort(key=lambda h: abs(h["contribution"] or 0.0), reverse=True)

    period, notes = score_period(prices, w, previous, key, nxt, project)
    return {
        "run_id": run_id,
        "as_of": key,
        "next_as_of": nxt,
        "index": i,
        "of": len(stops),
        "priced_from": str(d0),
        "priced_to": str(d1),
        "in_sample": (
            None if not conditions.get("split")
            else pd.Timestamp(key) < pd.Timestamp(conditions["split"])
        ),
        "period": asdict(period) if period else None,
        "notes": notes,
        "holdings": holdings,
        "opened": [h["symbol"] for h in holdings if h["was"] == 0 and h["weight"] != 0],
        "closed": [h["symbol"] for h in holdings if h["was"] != 0 and h["weight"] == 0],
    }


def pnl_ref(project: Project, alpha: str | Sequence[str]) -> str | None:
    """Where this run's PnL lands, if the project keeps a pnl stage.

    One alpha keeps its own name. Several alphas priced together land in one table
    named for all of them, so the graph shows what it really is: several weights
    tables feeding a single result.
    """
    stage = project.pnl_stage
    if stage is None:
        return None
    ids = alpha_ids_of(alpha) if isinstance(alpha, str) else list(alpha)
    if not ids:
        return None
    return f"{stage.id}." + "_".join(i.removeprefix("alpha_") for i in ids)


def _land_pnl(store: Store, project: Project, key: str, result: BacktestResult) -> None:
    ref = pnl_ref(project, key)
    if ref is None or not result.periods:
        return
    rows = pd.DataFrame([asdict(p) for p in result.periods])
    rows.insert(0, "alpha", key)
    rows["run_id"] = result.run_id
    rows["as_of"] = pd.to_datetime(rows["as_of"])
    rows["priced_from"] = pd.to_datetime(rows["priced_from"])
    rows["priced_to"] = pd.to_datetime(rows["priced_to"])
    equity, out = 1.0, []
    for n in rows["net"]:
        equity *= 1 + n
        out.append(equity)
    rows["equity"] = out
    store.write(ref, rows, mode="replace")


def compare(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """What moved between two backtests, and whether they even asked the same question."""
    keys = ("net", "gross", "fees", "slippage", "turnover", "periods")
    moved = {}
    for k in keys:
        av, bv = a.get(k), b.get(k)
        if av is None or bv is None:
            moved[k] = {"a": av, "b": bv, "delta": None}
        else:
            moved[k] = {"a": av, "b": bv, "delta": bv - av}
    same = a.get("digest") == b.get("digest")
    return {
        "a": a.get("run_id"),
        "b": b.get("run_id"),
        "same_question": same,
        "note": (
            "same inputs and same window, so any difference here is the engine"
            if same
            else "different digest: the project, the window or the seed changed between these runs"
        ),
        "moved": moved,
    }


__all__ = ["BacktestError", "BacktestResult", "Period", "compare", "dates", "decay_weights",
           "period_detail", "pnl_ref", "run_backtest", "score", "segment"]
