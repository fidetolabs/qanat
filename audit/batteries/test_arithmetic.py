"""The money arithmetic, against answers worked out before the run.

None of this data is realistic. Every symbol compounds at a fixed rate, so the
right answer is `(1 + rate) ** periods - 1` and there is nothing to interpret. An
engine that disagrees is wrong; that is the whole design.

This is the half of the audit that protects what is already correct. It found
nothing the first time -- net matched to 1.6e-13 -- and that result is exactly why
it is worth re-running before every release.
"""

import pytest
from lab import data, harness

G = 0.001                       # +0.1% a day, every day, for everyone
CAL = data.calendar(200)


def _run(tmp_path, name, bars, alpha, **kw):
    root = harness.build(tmp_path, name, bars, alpha, **kw)
    project, r, store, _rep, results = harness.run(root)
    assert all(x.ok for x in results), [x.error for x in results if not x.ok]
    return project, r, store


def test_net_is_the_compounded_answer_and_nothing_else(tmp_path):
    project, r, store = _run(tmp_path, "arith", data.drift(CAL, {"A": G, "B": G}),
                             harness.HOLD_ONE.format(symbol="A"), rebalance="1d")
    bt = harness.backtest(store, project, r, "2024-01-10", "2024-06-01")
    n = len(bt.periods)
    assert n > 100, "not enough periods for this to mean anything"
    assert bt.totals["net"] == pytest.approx((1 + G) ** n - 1, abs=1e-9)
    assert bt.periods[0].gross == pytest.approx(G)
    assert bt.periods[0].turnover == pytest.approx(1.0), "bought from flat"
    assert bt.periods[1].turnover == pytest.approx(0.0), "held, so nothing traded"
    store.close()


def test_fees_and_slippage_are_charged_on_turnover(tmp_path):
    """Alternate between two names, so every period turns the book over twice."""
    alternate = ('import pandas as pd\n'
                 'def run(ctx):\n'
                 '    df = ctx.read("normalized.prices")\n'
                 '    if df.empty:\n'
                 '        return pd.DataFrame(columns=["symbol", "weight", "as_of"])\n'
                 '    d = pd.Timestamp(df["date"].max())\n'
                 '    pick = "A" if d.toordinal() % 2 == 0 else "B"\n'
                 '    return pd.DataFrame([{"symbol": pick, "weight": 1.0,\n'
                 '                          "as_of": df["date"].max()}])\n')
    project, r, store = _run(tmp_path, "costs", data.drift(CAL, {"A": G, "B": G}),
                             alternate, rebalance="1d", fee=10.0, slip=5.0)
    bt = harness.backtest(store, project, r, "2024-01-10", "2024-06-01")
    mid = bt.periods[3]
    assert mid.turnover == pytest.approx(2.0), "sold one, bought the other"
    assert mid.fees == pytest.approx(2.0 * 10 / 10_000)
    assert mid.slippage == pytest.approx(2.0 * 5 / 10_000)
    assert mid.net == pytest.approx(G - 2.0 * 15 / 10_000)
    t = bt.totals
    assert t["net_sum"] == pytest.approx(t["gross"] - t["fees"] - t["slippage"])
    store.close()


def test_a_name_that_stops_being_priced_is_not_counted_as_held(tmp_path):
    """The bias a point-in-time engine exists to prevent, arriving by a side door:
    a delisted name used to earn zero while still being reported as a holding."""
    bars = data.delisting(CAL, at=100, rate=G)
    project, r, store = _run(tmp_path, "delist", bars, harness.EQUAL_WEIGHT, rebalance="5d")
    bt = harness.backtest(store, project, r, "2024-01-10", "2024-07-15")
    after = [p for p in bt.periods if p.as_of > str(CAL[102])]
    assert after, "the window has to reach past the delisting"
    assert {p.holdings for p in after} == {3}, "D has no price, so three names were held"
    assert any("no price" in n for n in bt.notes)
    store.close()


@pytest.mark.parametrize(("knob", "value"), [("purge", "5d"), ("embargo", "3d")])
def test_the_knobs_shift_by_exactly_what_they_say(tmp_path, knob, value):
    import pandas as pd

    p0, r0, s0 = _run(tmp_path, f"{knob}_off", data.drift(CAL, {"A": G, "B": G}),
                      harness.HOLD_ONE.format(symbol="A"), rebalance="10d")
    p1, r1, s1 = _run(tmp_path, f"{knob}_on", data.drift(CAL, {"A": G, "B": G}),
                      harness.HOLD_ONE.format(symbol="A"), rebalance="10d", **{knob: value})
    a = harness.backtest(s0, p0, r0, "2024-03-01", "2024-04-01")
    b = harness.backtest(s1, p1, r1, "2024-03-01", "2024-04-01")
    days = int(value.rstrip("d"))
    if knob == "embargo":
        # the period fills `days` later at both ends
        assert (pd.Timestamp(b.periods[0].priced_from)
                - pd.Timestamp(a.periods[0].priced_from)).days == days
    else:
        # purge holds rows back from the step, not from the scorer, so the same
        # window still closes the same number of periods
        assert len(a.periods) == len(b.periods)
    s0.close()
    s1.close()


def test_blending_two_alphas_is_a_weighted_sum(tmp_path):
    """A blend's gross is the allocation times each alpha's gross. Nothing else."""
    ga, gb = 0.002, 0.0005
    bars = data.drift(CAL, {"A": ga, "B": gb})
    extra = ("  - id: alpha_g\n"
             "    from: [normalized.prices]\n"
             "    to: [weights.g]\n"
             "    script: steps/beta.py\n"
             "    universe: u\n")
    root = harness.build(tmp_path, "blend", bars, harness.HOLD_ONE.format(symbol="A"),
                         files={"beta.py": harness.HOLD_ONE.format(symbol="B")},
                         rebalance="1d", extra_steps=extra)
    project, r, store, _rep, results = harness.run(root)
    assert all(x.ok for x in results), [x.error for x in results if not x.ok]
    for alloc, wa, wb in [(None, 0.5, 0.5), ({"h": 3, "g": 1}, 0.75, 0.25)]:
        bt = harness.backtest(store, project, r, "2024-02-01", "2024-05-01",
                              alpha=["alpha_h", "alpha_g"], allocation=alloc)
        assert bt.periods[5].gross == pytest.approx(wa * ga + wb * gb, abs=1e-12)
    store.close()
