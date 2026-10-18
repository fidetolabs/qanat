"""Replay: the clock, the bill, and the two things that must not happen."""

from pathlib import Path

import pandas as pd
import pytest

from qanat.backtest import BacktestError, dates, run_backtest
from qanat.project import load
from qanat.runner import run_all
from qanat.scaffold import write_project
from qanat.store import Store

#: The scaffold ships 420 days. A replay re-runs the whole pipeline once per
#: rebalance date inside the window, so what this file costs is stops, not rows --
#: and `_window` takes half the history, which at `rebalance: 5d` was ~39 of them
#: per test, 36 times over. A shorter feed leaves enough stops for every assertion
#: here at a fraction of the work. Pass `days=` if a test needs a longer history.
TEST_DAYS = 150


def _ready(tmp_path: Path, days: int = TEST_DAYS) -> tuple[Store, object, Path]:
    """A scaffolded project, run once. `qanat init` ships the backtest block."""
    write_project(tmp_path, "demo")
    cfg = tmp_path / "qanat.yaml"
    cfg.write_text(cfg.read_text().replace("days: 420", f"days: {days}"))
    project, root = load(tmp_path)
    assert project.backtest is not None, "the scaffold should ship a backtest block"
    store = Store(project.store_url(root))
    assert all(r.ok for r in run_all(store, project, root))
    return store, project, root


def _add_weights_step(tmp_path: Path, name: str) -> str:
    """A second step writing its own weights table, so a blend has two to hold.

    The shelf used to supply these. It is gone, and nothing replaced it, because a
    second weights table is an ordinary step: copy the one the scaffold wrote and
    point it at a table of its own.
    """
    from qanat.editor import save_step

    project, root = load(tmp_path)
    base = next(st for st in project.steps
                if any(w.startswith("weights.") for w in st.writes))
    script = f"steps/{name}.py"
    (root / script).write_text((root / base.script).read_text())
    save_step(project, root, {
        "id": name, "from": list(base.reads), "to": [f"weights.{name}"],
        "script": script, "universe": base.universe,
        "options": dict(base.options),
    }, create_script=False)
    return name


def _window(store: Store) -> dict:
    """A window that sits inside whatever dates the synthetic feed produced."""
    bars = store.read("normalized.prices")
    lo, hi = bars["date"].min(), bars["date"].max()
    start = lo + (hi - lo) * 0.5
    return {"frm": str(start.normalize()), "to": str((hi - pd.Timedelta(days=12)).normalize())}


# ---------------------------------------------------------------------- dates
def test_dates_cover_both_ends():
    d = dates("2024-01-01", "2024-01-11", "5d")
    assert [x[:10] for x in d] == ["2024-01-01", "2024-01-06", "2024-01-11"]


def test_a_backwards_window_is_refused():
    with pytest.raises(BacktestError):
        dates("2024-02-01", "2024-01-01", "1d")


# ------------------------------------------------------------------ the clock
def test_a_step_replayed_cannot_see_past_the_as_of_date(tmp_path: Path):
    store, project, _ = _ready(tmp_path)
    bars = store.read("normalized.prices")
    cut = str((bars["date"].min() + (bars["date"].max() - bars["date"].min()) * 0.5).normalize())

    store.open_pit(cut, project.time_columns)
    try:
        seen = store.query("SELECT max(CAST(date AS TIMESTAMP)) AS m FROM normalized__prices")["m"][0]
    finally:
        store.close_pit()

    assert pd.Timestamp(seen) <= pd.Timestamp(cut)
    # and the table itself was never touched
    assert store.read("normalized.prices")["date"].max() == bars["date"].max()
    store.close()


def test_ctx_read_is_cut_at_the_as_of_date(tmp_path: Path):
    store, _project, _ = _ready(tmp_path)
    bars = store.read("normalized.prices")
    cut = str(bars["date"].min() + pd.Timedelta(days=30))
    assert len(store.read("normalized.prices", as_of=cut)) < len(bars)
    assert store.read("normalized.prices", as_of=cut)["date"].max() <= pd.Timestamp(cut)
    store.close()


def test_a_step_that_writes_the_future_fails(tmp_path: Path):
    store, _project, _ = _ready(tmp_path)
    (tmp_path / "steps" / "momentum.py").write_text(
        "import pandas as pd\n"
        "def run(ctx):\n"
        "    return pd.DataFrame({'symbol': ['AAA'], 'momentum': [1.0],\n"
        "                         'as_of': [pd.Timestamp('2099-01-01')]})\n"
    )
    project, root = load(tmp_path)
    cut = str(store.read("normalized.prices")["date"].max())
    store.open_pit(cut, project.time_columns)
    try:
        results = {r.job_id: r for r in run_all(store, project, root, as_of=cut, sources=False)}
    finally:
        store.close_pit()
    assert results["momentum"].status == "failed"
    assert "lookahead" in results["momentum"].error
    store.close()


# ------------------------------------------------------------------- the bill
def test_a_replay_prices_what_it_held(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d", seed=1)

    assert res.periods, res.notes + res.failures
    assert not res.failures, res.failures
    t = res.totals
    # The accounting identity holds on the additive view: gross, fees and slippage
    # are per-period amounts, so they reconcile against the sum of the periods.
    assert t["net_sum"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"])
    # and the headline is what the money did -- the periods compound, and it agrees
    # with the equity curve drawn beside it
    assert t["net"] == pytest.approx(t["equity"] - 1.0)
    assert t["fees"] > 0 and t["slippage"] > 0  # this project charges both
    assert t["slippage"] == pytest.approx(t["fees"] * 2)  # 10bps against 5bps
    assert store.bt_weights(res.run_id)
    store.close()


def test_the_same_seed_gives_the_same_number(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    window = _window(store)
    a = run_backtest(store, project, root, **window, rebalance="10d", seed=3)
    b = run_backtest(store, project, root, **window, rebalance="10d", seed=3)
    assert a.digest == b.digest
    assert a.totals["net"] == pytest.approx(b.totals["net"])
    assert a.run_id != b.run_id  # same answer, different run
    store.close()


def test_a_replay_puts_the_tables_back(tmp_path: Path):
    """A backtest reads the past. It must not leave the tables stuck there."""
    store, project, root = _ready(tmp_path)
    before = {ref: store.table_info(ref).rows for ref in project.tables()}
    run_backtest(store, project, root, **_window(store), rebalance="10d")
    after = {ref: store.table_info(ref).rows for ref in project.tables()}
    assert after == before
    store.close()


def test_a_project_with_no_backtest_block_says_so(tmp_path: Path):
    write_project(tmp_path, "demo")
    yaml = tmp_path / "qanat.yaml"
    yaml.write_text(yaml.read_text().split("backtest:")[0])  # take the block back off
    project, root = load(tmp_path)
    assert project.backtest is None
    store = Store(project.store_url(root))
    run_all(store, project, root)
    with pytest.raises(BacktestError, match="backtest"):
        run_backtest(store, project, root, "2026-01-01", "2026-02-01")
    store.close()


def test_a_window_with_one_stop_is_refused(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    with pytest.raises(BacktestError, match="at least two"):
        run_backtest(store, project, root, "2026-01-01", "2026-01-01", rebalance="10d")
    store.close()


# ------------------------------------------------------------- the conditions
def test_decay_blends_the_last_portfolios():
    from qanat.backtest import decay_weights

    stops = ["a", "b", "c"]
    held = {
        "a": pd.Series({"X": 1.0}),
        "b": pd.Series({"Y": 1.0}),
        "c": pd.Series({"X": 1.0}),
    }
    out = decay_weights(held, stops, 2)
    # newest carries 2/3, the one before it 1/3
    assert out["b"]["Y"] == pytest.approx(2 / 3)
    assert out["b"]["X"] == pytest.approx(1 / 3)
    assert decay_weights(held, stops, 1) is held  # off


def test_decay_cuts_turnover(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    window = _window(store)
    raw = run_backtest(store, project, root, **window, rebalance="10d", seed=2)
    smooth = run_backtest(store, project, root, **window, rebalance="10d", seed=2, decay=3)
    assert smooth.totals["turnover"] < raw.totals["turnover"]
    # a different question deserves a different digest
    assert smooth.digest != raw.digest
    assert smooth.conditions["decay"] == 3
    store.close()


def test_an_unknown_universe_is_refused(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    with pytest.raises(BacktestError, match="unknown universe"):
        run_backtest(store, project, root, **_window(store), rebalance="10d", universe="nope")
    store.close()


# ----------------------------------------------------------- scoring as we go
def test_scoring_as_we_go_matches_scoring_at_the_end(tmp_path: Path):
    """The replay closes each period the moment it can. That has to be the same
    arithmetic as scoring the whole set afterwards, or the live curve is a lie."""
    from qanat.backtest import totals_of

    store, project, root = _ready(tmp_path)
    window = _window(store)
    streamed = run_backtest(store, project, root, **window, rebalance="10d", seed=4, decay=2)

    # Rebuild the portfolios from what the run recorded, and score them in one go.
    # `bt_weights` holds what was actually *priced*, decay already applied -- it used
    # to hold the step's raw output, so the drill-down showed a basket that was never
    # traded. Nothing here re-applies decay for that reason.
    rows = store.bt_weights(streamed.run_id)
    held: dict = {}
    for r in rows:
        held.setdefault(str(r["as_of"]), {})[r["symbol"]] = r["weight"]
    assert len(held) == len(streamed.periods) + 1   # every stop but the last opens a period

    # Totals recomputed from the periods the run recorded have to match the totals
    # the run reported while it was still going.
    totals = totals_of(streamed.periods, streamed.rebalance)
    assert totals["net"] == pytest.approx(streamed.totals["net"])
    assert totals["turnover"] == pytest.approx(streamed.totals["turnover"])
    store.close()


def test_each_closed_period_is_a_row_while_the_run_is_still_going(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d")
    rows = store.bt_periods(res.run_id)
    assert len(rows) == len(res.periods)
    assert rows[0]["net"] == pytest.approx(res.periods[0].net)
    store.close()


def test_progress_carries_the_curve_as_it_grows(tmp_path: Path):
    from qanat import progress

    store, project, root = _ready(tmp_path)
    seen: list[int] = []
    run_backtest(store, project, root, **_window(store), rebalance="10d",
                 on_step=lambda stop, n: seen.append(len(progress.snapshot()["periods"])))
    # the curve is longer at the end of the walk than at the start of it
    assert seen[-1] > seen[1]
    snap = progress.snapshot()
    assert snap["running"] is False
    assert snap["totals"]["periods"] == len(snap["periods"])
    store.close()


def test_a_step_that_writes_nothing_clears_its_table(tmp_path: Path):
    """"I computed nothing" must not read downstream as "here is a fresh answer".
    During a replay those leftover rows are from the future, which is the whole
    thing the as-of cursor exists to prevent."""
    import pandas as pd_

    store, _project, _ = _ready(tmp_path)
    assert len(store.read("features.momentum")) > 0

    store.write("features.momentum", pd_.DataFrame())
    assert len(store.read("features.momentum")) == 0
    assert store.exists("features.momentum")  # the schema survives; only the rows go
    store.close()


def test_a_window_that_starts_too_early_says_so(tmp_path: Path):
    store, project, root = _ready(tmp_path)
    bars = store.read("normalized.prices")
    res = run_backtest(store, project, root, frm=str(bars["date"].min().normalize()),
                       to=str((bars["date"].min() + pd.Timedelta(days=60)).normalize()),
                       rebalance="10d")
    # the first stop has no history behind it, and is reported as flat rather than
    # as a failure or as last pass's portfolio
    assert any("no portfolio" in n or "no history" in n for n in res.notes), res.notes
    store.close()


# ------------------------------------------------------- in sample / out of it
def test_a_period_opens_up_into_the_names_that_made_it(tmp_path: Path):
    """The curve says a period made x. This has to say which names made it, and the
    two have to agree -- a drill-down that does not reconcile is decoration."""
    from qanat.backtest import period_detail

    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d", seed=6)
    target = res.periods[len(res.periods) // 2]

    d = period_detail(store, project, res.run_id, target.as_of)
    assert d["next_as_of"] > d["as_of"]
    contributions = sum(h["contribution"] or 0.0 for h in d["holdings"])
    assert contributions == pytest.approx(target.gross, abs=1e-9)
    traded = sum(abs(h["traded"]) for h in d["holdings"])
    assert traded == pytest.approx(target.turnover, abs=1e-9)
    assert d["period"]["net"] == pytest.approx(target.net)
    store.close()


def test_the_last_stop_has_no_period_to_open(tmp_path: Path):
    from qanat.backtest import period_detail

    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d")
    last = max(str(r["as_of"]) for r in store.bt_weights(res.run_id))
    with pytest.raises(BacktestError, match="never closed"):
        period_detail(store, project, res.run_id, last)
    store.close()


# ------------------------------------------------------------- the fill price
def test_a_portfolio_is_never_filled_at_a_price_it_used_to_decide(tmp_path: Path):
    """The one that decides whether any of this is worth reading.

    A step deciding on the close of day t decided *using* that close. The order
    does not exist until that price is already printed, so it cannot be filled
    there. Every period must enter strictly after its own as-of date.
    """
    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d")
    assert res.periods
    for p in res.periods:
        assert pd.Timestamp(p.priced_from) > pd.Timestamp(p.as_of), (
            f"{p.as_of} was filled at {p.priced_from} — a price the step had already seen"
        )
        assert pd.Timestamp(p.priced_to) > pd.Timestamp(p.priced_from)
    store.close()


def test_the_drill_down_prices_the_same_way(tmp_path: Path):
    from qanat.backtest import period_detail

    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="10d")
    d = period_detail(store, project, res.run_id, res.periods[0].as_of)
    assert pd.Timestamp(d["priced_from"]) > pd.Timestamp(d["as_of"])
    assert d["priced_from"] == res.periods[0].priced_from
    store.close()


# ----------------------------------------------------------- several at once
def test_two_steps_are_priced_as_one_portfolio(tmp_path: Path):
    """The point of the change: a run holds the sum of several books, and the one
    PnL table it lands in is named for all of them."""
    from qanat.backtest import pnl_ref

    store, project, root = _ready(tmp_path)
    for name in ("second", "third"):
        _add_weights_step(tmp_path, name)
    project, root = load(tmp_path)
    assert all(r.ok for r in run_all(store, project, root))
    both = [a for a, _ in project.weights_steps][:2]

    res = run_backtest(store, project, root, **_window(store), rebalance="5d", step=both)
    assert res.conditions["step"] == "+".join(both)
    assert len(res.conditions["steps"]) == 2
    assert sum(res.conditions["allocation"].values()) == pytest.approx(1.0)

    ref = pnl_ref(project, res.conditions["step"])
    assert ref and "_" in ref.partition(".")[2]
    assert store.exists(ref), "a blend needs a table of its own, not one of its parts'"

    # what was held is one book of the usual size, not two books stacked
    for stop in {w["as_of"] for w in store.bt_weights(res.run_id)}:
        held = [w for w in store.bt_weights(res.run_id, str(stop)[:10])]
        if held:
            assert sum(abs(w["weight"]) for w in held) == pytest.approx(1.0, abs=1e-6)
    store.close()


def test_the_order_you_name_them_in_does_not_make_a_different_run(tmp_path: Path):

    store, project, root = _ready(tmp_path)
    _add_weights_step(tmp_path, "second")
    project, root = load(tmp_path)
    assert all(r.ok for r in run_all(store, project, root))
    a, b = [x for x, _ in project.weights_steps][:2]

    w = _window(store)
    one = run_backtest(store, project, root, **w, rebalance="5d", step=[a, b])
    two = run_backtest(store, project, root, **w, rebalance="5d", step=f"{b},{a}")
    assert one.conditions["step"] == two.conditions["step"]
    assert one.digest == two.digest, "the same question has to have the same digest"
    store.close()


def test_an_allocation_is_a_ratio_not_a_requirement_to_do_the_division(tmp_path: Path):

    store, project, root = _ready(tmp_path)
    _add_weights_step(tmp_path, "second")
    project, root = load(tmp_path)
    assert all(r.ok for r in run_all(store, project, root))
    picked = [(a, r) for a, r in project.weights_steps][:2]
    names = [project.weights_name(a, r) for a, r in picked]

    res = run_backtest(store, project, root, **_window(store), rebalance="5d",
                       step=[a for a, _ in picked],
                       allocation={names[0]: 3, names[1]: 1})
    assert res.conditions["allocation"][names[0]] == pytest.approx(0.75)

    with pytest.raises(BacktestError):
        run_backtest(store, project, root, **_window(store), rebalance="5d",
                     step=[a for a, _ in picked], allocation={"nope": 1})
    store.close()


# ---------------------------------------- how a step wants to be run
def test_a_step_carries_its_own_rebalance_and_decay(tmp_path: Path):
    """A five-day reversal and a sixty-day momentum do not want the same gap, so
    the gap belongs to the step, not to the project."""
    from qanat.project_io import save_project

    store, project, root = _ready(tmp_path)
    step = next(st for st in project.steps if st.id == project.weights_steps[0][0])
    step.rebalance, step.decay = "10d", 3
    save_project(project, root)
    project, root = load(tmp_path)          # and it survives the round trip
    assert next(st for st in project.steps if st.id == step.id).rebalance == "10d"

    res = run_backtest(store, project, root, **_window(store), step=step.id)
    assert res.conditions["rebalance"] == "10d"
    assert res.conditions["decay"] == 3
    # what the caller asks for still wins
    res2 = run_backtest(store, project, root, **_window(store), step=step.id, rebalance="5d")
    assert res2.conditions["rebalance"] == "5d"
    store.close()


def test_costs_are_a_condition_of_the_run(tmp_path: Path):
    """Raising the fee has to change the answer and the digest, or 'where does this
    edge die' cannot be asked."""
    store, project, root = _ready(tmp_path)
    w = _window(store)
    cheap = run_backtest(store, project, root, **w, rebalance="5d", fee_bps=0, slippage_bps=0)
    dear = run_backtest(store, project, root, **w, rebalance="5d", fee_bps=50, slippage_bps=50)
    assert dear.totals["net"] < cheap.totals["net"]
    assert dear.digest != cheap.digest, "two different questions must not share a digest"
    assert dear.conditions["fee_bps"] == 50
    store.close()


def test_two_steps_that_disagree_about_the_gap_must_be_told(tmp_path: Path):
    from qanat.project_io import save_project

    store, project, root = _ready(tmp_path)
    _add_weights_step(tmp_path, "second")
    project, root = load(tmp_path)
    a, b = [x for x, _ in project.weights_steps][:2]
    for st in project.steps:
        if st.id == a:
            st.rebalance = "5d"
        elif st.id == b:
            st.rebalance = "20d"
    save_project(project, root)
    project, root = load(tmp_path)
    assert all(r.ok for r in run_all(store, project, root))

    with pytest.raises(BacktestError, match="different rebalance"):
        run_backtest(store, project, root, **_window(store), step=[a, b])
    # saying which settles it
    ok = run_backtest(store, project, root, **_window(store), step=[a, b], rebalance="5d")
    assert ok.conditions["rebalance"] == "5d"
    store.close()


# ------------------------------------------ an edit does not delete what it wrote
def test_an_edit_makes_downstream_stale_without_deleting_it(tmp_path: Path):
    """Deleting rows because a script changed would throw away the one copy of a
    slow computation. They stop being trustworthy, not present."""
    from qanat.plan import plan as plan_project

    store, project, root = _ready(tmp_path)
    before = store.table_info("normalized.prices").rows
    assert before > 0

    script = root / "steps" / "normalize.sql"
    script.write_text(script.read_text() + "\n-- a different rule\n")
    project, root = load(tmp_path)
    pl = plan_project(project, root, store)
    stale = pl.stale(project)

    assert "normalized.prices" in stale, "the table the edited step writes is stale"
    assert any(r.startswith("weights.") for r in stale), "staleness travels downstream"
    assert store.table_info("normalized.prices").rows == before, "nothing was deleted"
    store.close()


# ------------------------------------------------- what the money actually did
def test_net_compounds_and_reconciles(tmp_path: Path):
    """`net` is what the money did, so the periods compound. The additive view is
    kept beside it, because gross, fees and slippage are per-period amounts and
    have to add up to something."""
    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="5d")
    t = res.totals

    grown = 1.0
    for p_ in res.periods:
        grown *= 1 + p_.net
    assert t["net"] == pytest.approx(grown - 1.0)
    assert t["equity"] == pytest.approx(grown), "the headline and the curve are one number"
    assert t["net_sum"] == pytest.approx(sum(p_.net for p_ in res.periods))
    assert t["net_sum"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"])
    store.close()


# --------------------------------------------- who was in the universe, and when
def test_a_universe_with_dates_is_point_in_time(tmp_path: Path):
    """The survivorship fix: a replay must hold the names that were investable on
    the day it decided, not the ones that survived to today."""
    import pandas as pd

    from qanat.context import is_point_in_time, members

    df = pd.DataFrame({
        "symbol": ["DELISTED", "ALWAYS", "LISTED_LATER"],
        "from": ["2010-01-01", "", "2026-01-01"],
        "to": ["2020-06-01", "", ""],
    })
    assert is_point_in_time(df)
    assert set(members(df, "2015-01-01")["symbol"]) == {"DELISTED", "ALWAYS"}
    assert set(members(df, "2021-01-01")["symbol"]) == {"ALWAYS"}
    assert set(members(df, "2026-06-01")["symbol"]) == {"ALWAYS", "LISTED_LATER"}
    # with no as-of there is nothing to filter by, so the whole file stands
    assert len(members(df, None)) == 3

    plain = pd.DataFrame({"symbol": ["A", "B"]})
    assert not is_point_in_time(plain)
    assert len(members(plain, "2015-01-01")) == 2, "a list with no dates is the same every day"


def test_check_says_when_a_universe_would_flatter_the_numbers(tmp_path: Path):
    from qanat.project import validate

    store, project, root = _ready(tmp_path)
    rep = validate(project, root)
    assert rep.ok
    assert not any("survivorship" in w for w in rep.warnings), (
        "the scaffold ships membership dates, so a new project is not warned"
    )

    # strip the dates and the warning must appear
    csv = root / "universes" / "demo8.csv"
    rows = [ln.split(",") for ln in csv.read_text().strip().split("\n")]
    csv.write_text("\n".join(",".join(r[:3]) for r in rows) + "\n")
    rep = validate(project, root)
    assert any("survivorship" in w for w in rep.warnings), rep.warnings
    assert rep.ok, "it is a warning, not an error -- a static list is still runnable"
    store.close()


def test_a_replay_stops_holding_a_name_the_day_it_leaves(tmp_path: Path):
    """The survivorship fix, end to end. Not "the helper filters a dataframe" —
    a replay across a delisting must actually stop holding the name."""

    # names a delisting on 2026-03-01 and replays across it, so it needs a feed
    # that reaches back that far rather than the short one the rest of us use
    store, project, root = _ready(tmp_path, days=420)
    csv = root / "universes" / "demo8.csv"
    rows = [ln.split(",") for ln in csv.read_text().strip().split("\n")]
    head, body = rows[0], rows[1:]
    leaves = body[0][0]
    for r in body:
        if r[0] == leaves:
            r[4] = "2026-03-01"
    csv.write_text("\n".join(",".join(r) for r in [head, *body]) + "\n")

    _add_weights_step(tmp_path, "second")
    project, root = load(tmp_path)
    assert all(r.ok for r in run_all(store, project, root))

    res = run_backtest(store, project, root, frm="2026-01-01", to="2026-08-01",
                       rebalance="5d", step=project.weights_step("portfolio")[0])
    held = store.bt_weights(res.run_id)
    before = [w for w in held if w["symbol"] == leaves and str(w["as_of"])[:10] < "2026-03-01"]
    after = [w for w in held if w["symbol"] == leaves and str(w["as_of"])[:10] >= "2026-03-01"]
    assert before, f"{leaves} should have been holdable while it was still listed"
    assert not after, f"{leaves} was held after it left the universe — survivorship bias is back"
    store.close()


def test_prune_does_not_delete_backtest_results(tmp_path: Path):
    """A PnL table is written by a replay, so nothing in the file declares it. If
    plan calls that an orphan, `qanat prune` deletes the one thing in the store
    that running the pipeline again cannot rebuild."""
    from qanat.plan import plan as plan_project

    store, project, root = _ready(tmp_path)
    res = run_backtest(store, project, root, **_window(store), rebalance="5d")
    landed = [t for t in store.all_tables() if t.startswith("pnl.")]
    assert landed, "the replay should have landed a pnl table"

    pl = plan_project(project, root, store)
    orphans = {c.target for c in pl.orphans}
    assert not (orphans & set(landed)), f"prune would delete {orphans & set(landed)}"

    # a table nothing produces is still an orphan
    store.write("features.junk", store.read(project.backtest.prices).head(3))
    pl = plan_project(project, root, store)
    assert "features.junk" in {c.target for c in pl.orphans}
    assert res.run_id
    store.close()


# --------------------------------------------- scoring forward as data arrives
def _live(root: Path, **fields) -> None:
    import yaml

    f = root / "qanat.yaml"
    spec = yaml.safe_load(f.read_text())
    spec["backtest"].update({"live": True, **fields})
    f.write_text(yaml.safe_dump(spec, sort_keys=False))


def test_the_grid_decides_when_a_live_run_is_due(tmp_path: Path):
    """New rows are not the trigger. A rebalance date is -- so a live run lands
    on the same as-of dates a single replay over the window would have used."""
    from qanat.backtest import next_stop

    assert next_stop("2026-01-01", "2026-01-01", "5d") == str(pd.Timestamp("2026-01-06"))
    assert next_stop("2026-01-01", "2026-01-04", "5d") == str(pd.Timestamp("2026-01-06"))
    assert next_stop("2026-01-01", "2026-01-06", "5d") == str(pd.Timestamp("2026-01-11"))
    # a day's rebalance gives a stop a day
    assert next_stop("2026-01-01", "2026-01-01", "1d") == str(pd.Timestamp("2026-01-02"))


def _ps(nets, start="2026-01-05"):
    import pandas as pd

    from qanat.backtest import Period

    day = pd.Timestamp(start)
    out = []
    for i, n in enumerate(nets):
        a, b = day + pd.Timedelta(days=5 * i), day + pd.Timedelta(days=5 * (i + 1))
        out.append(Period(as_of=str(a.date()), priced_from=str(a.date()), priced_to=str(b.date()),
                          holdings=3, gross=n, turnover=0.1, fees=0.0, slippage=0.0, net=n))
    return out


def test_per_year_comes_from_the_rebalance_gap():
    """A period is a rebalance, not a day. 252 would call a weekly rule five times
    more volatile than it is."""
    from qanat.backtest import per_year

    assert per_year("1d") == 365.0
    assert round(per_year("5d"), 2) == 73.0
    assert round(per_year("1w"), 2) == 52.14
    assert per_year("nonsense") == 252.0          # falls back rather than raising


def test_max_drawdown_is_a_property_of_the_path():
    """Two runs can end at the same place having been to very different ones."""
    from qanat.backtest import totals_of

    steady = totals_of(_ps([0.01] * 4), "5d")
    bumpy = totals_of(_ps([0.25, -0.20, 0.25, 1.01 ** 4 / 1.25 - 1]), "5d")
    assert steady["max_drawdown"] == 0.0
    assert bumpy["max_drawdown"] < -0.15
    # and the totals alone would not have told you
    assert abs(steady["net"] - bumpy["net"]) < 0.02
