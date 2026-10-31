"""The screen arithmetic, against answers worked out before the run.

None of this data is realistic. Every symbol compounds at a fixed rate, so the
right answer is known in closed form and there is nothing to interpret. An
engine that disagrees is wrong; that is the whole design.

This is the half of the audit that protects what is already correct.

**What it used to check is gone.** It checked money: net compounding, fees and
slippage charged on turnover, two baskets blended as a weighted sum. Nothing
in the package prices a basket any more, so the arithmetic that survives is
the forward return — one company, its own two dates — and the threshold
comparison a clause makes.
"""

import pytest
from lab import data, harness

from qanat.screen import forward_returns, parse_filters, screen

G = 0.001                       # +0.1% a day, every day, for everyone
CAL = data.calendar(200)


def _run(tmp_path, name, bars, **kw):
    root = harness.build(tmp_path, name, bars, harness.HOLD_ONE.format(symbol="A"), **kw)
    project, r, store, _rep, results = harness.run(root)
    assert all(x.ok for x in results), [x.error for x in results if not x.ok]
    return project, r, store


@pytest.mark.audit
def test_a_forward_return_is_the_two_prices_and_nothing_else(tmp_path):
    """`next_price / price - 1`, between one company's own consecutive dates.
    At a fixed daily rate every one of them is exactly that rate."""
    project, _r, store = _run(tmp_path, "fwd", data.drift(CAL, {"A": G, "B": G}))
    try:
        fwd = forward_returns(store, project)
        a = fwd[fwd["subject"] == "A"].dropna(subset=["forward_return"])
        assert len(a) > 100
        for got in a["forward_return"]:
            assert got == pytest.approx(G, abs=1e-12)
    finally:
        store.close()


@pytest.mark.audit
def test_the_last_date_has_no_forward_return_rather_than_a_zero(tmp_path):
    """Nothing comes after it. Zero would be a claim; absent is the truth."""
    project, _r, store = _run(tmp_path, "tail", data.drift(CAL, {"A": G}))
    try:
        fwd = forward_returns(store, project).sort_values("as_of")
        assert fwd["forward_return"].iloc[-1] != fwd["forward_return"].iloc[-1]  # NaN
    finally:
        store.close()


@pytest.mark.audit
def test_each_company_is_priced_on_its_own_dates(tmp_path):
    """Two symbols on different calendars must not be forced onto a shared one."""
    bars = data.drift(CAL, {"A": G, "B": G})
    bars = bars[~((bars["symbol"] == "B") & (bars["date"].isin(sorted(bars["date"])[:40])))]
    project, _r, store = _run(tmp_path, "own", bars)
    try:
        fwd = forward_returns(store, project)
        first = fwd.groupby("subject")["as_of"].min()
        assert first["B"] > first["A"], "B starts later and its returns should too"
        for got in fwd.dropna(subset=["forward_return"])["forward_return"]:
            assert got == pytest.approx(G, abs=1e-12)
    finally:
        store.close()


@pytest.mark.audit
def test_a_threshold_is_applied_exactly_where_it_is_set(tmp_path):
    """A clause is a comparison. Off by one row is off by one row."""
    project, _r, store = _run(tmp_path, "thresh", data.drift(CAL, {"A": G, "B": G}))
    try:
        values = {r["subject"]: r for r in screen(
            store, project, parse_filters([{"metric": "px", "op": "<", "value": 1e9}]),
            as_of="2024-03-01")["rows"]}
        assert all(r["passed"] for r in values.values())
        none_pass = screen(store, project, parse_filters(
            [{"metric": "px", "op": "<", "value": 0}]), as_of="2024-03-01")
        assert not any(r["passed"] for r in none_pass["rows"])
    finally:
        store.close()


@pytest.mark.audit
def test_nothing_is_averaged_across_dates(tmp_path):
    """A number that cannot be read off one date's list is not in the answer."""
    project, _r, store = _run(tmp_path, "noavg", data.drift(CAL, {"A": G, "B": G}))
    try:
        out = screen(store, project, parse_filters(
            [{"metric": "px", "op": ">", "value": 0}]),
            frm="2024-02-01", to="2024-06-01", every="30d")
        assert set(out) <= {"rows", "counts", "applied", "execution", "notes"}
        assert len(out["counts"]) > 1, "this is a window, so there is more than one date"

        # Every number in the answer belongs to one date. `values` is that
        # subject on that date; `execution` is metadata about the call and
        # carries no number read off more than one date.
        dates = [c["as_of"] for c in out["counts"]]
        assert out["execution"]["dates"] == dates
        assert len(set(dates)) == len(dates), "a date is not visited twice"
        for row in out["rows"]:
            assert set(row["values"]) == {"px"}
            assert isinstance(row["values"]["px"], (float, type(None)))
        # And the same screen twice is still the same answer, `execution`
        # included -- which is only true because nothing in it is a wall clock.
        again = screen(store, project, parse_filters(
            [{"metric": "px", "op": ">", "value": 0}]),
            frm="2024-02-01", to="2024-06-01", every="30d")
        assert again == out
    finally:
        store.close()
