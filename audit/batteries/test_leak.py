"""Can a step reach data that had not happened yet?

The one question this tool has to answer correctly. Everything else is arithmetic
that can be checked by reading; leakage cannot, because a leaking alpha looks like
a brilliant one.

The data does the discriminating. Each day one of two symbols jumps 2% and the
other falls 2%, in a pattern with nothing to learn from the past -- so a blind
alpha earns roughly nothing, and an alpha that can see two days ahead earns 2% per
period, which compounds to something absurd. Every vector below runs the *same*
cheating logic and differs only in how it tries to reach the prices. The net is
the verdict.

    blind  ~3%      the honest baseline
    leak   ~1292%   what seeing the future was worth when this was written
"""

import pytest
from lab import data, harness

WINDOW = ("2024-01-20", "2024-06-01")
HONEST = 0.10          # a blind alpha lands far below this
LEAKING = 1.0          # a leaking one lands far above it

PICK_FROM_THE_FUTURE = '''
    piv = full.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
    piv = piv.sort_index()
    piv.index = pd.to_datetime(piv.index)
    later = piv.index[piv.index > pd.Timestamp(visible_max)]
    if len(later) < 2:
        pick = "A"
    else:
        d1, d2 = later[0], later[1]
        pick = "A" if piv.loc[d2, "A"] / piv.loc[d1, "A"] > piv.loc[d2, "B"] / piv.loc[d1, "B"] else "B"
    return pd.DataFrame([{"symbol": pick, "weight": 1.0, "as_of": visible_max}])
'''


def cheat(getter: str) -> str:
    """An alpha that tries to see two days ahead, however `getter` reaches the data.

    It writes an honest `as_of`, which is what makes this the realistic shape: the
    lookahead guard checks the timestamp a step *writes*, so a leak that stamps the
    right date sails past it.
    """
    return ("import pandas as pd\n"
            "def run(ctx):\n"
            '    legit = ctx.read("normalized.prices")\n'
            "    if legit.empty:\n"
            '        return pd.DataFrame(columns=["symbol", "weight", "as_of"])\n'
            '    visible_max = legit["date"].max()\n'
            f"    {getter}\n"
            "    if full is None or len(full) == 0:\n"
            '        return pd.DataFrame([{"symbol": "A", "weight": 1.0, "as_of": visible_max}])\n'
            + PICK_FROM_THE_FUTURE)


VECTORS = {
    "blind":                'full = None',
    "ctx.read":             'full = legit',
    "ctx.sql bare":         'full = ctx.sql("SELECT date, symbol, close FROM normalized__prices")',
    "ctx.sql raw":          'full = ctx.sql("SELECT date, symbol, close FROM raw__bars")',
    "ctx.sql main.":        'full = ctx.sql("SELECT date, symbol, close FROM main.raw__bars")',
    "ctx.store.read":       'full = ctx.store.read("raw.bars")',
}


def _net(tmp_path, name, getter):
    bars = data.alternating_winner(data.calendar(200))
    root = harness.build(tmp_path, name, bars, cheat(getter), rebalance="1d")
    project, r, store, _rep, results = harness.run(root)
    try:
        if any(not x.ok for x in results):
            return "refused", next(x.error for x in results if not x.ok)
        bt = harness.backtest(store, project, r, *WINDOW)
        return "ran", bt.totals.get("net")
    except Exception as exc:                      # noqa: BLE001 -- refusing is a pass
        return "refused", f"{type(exc).__name__}: {exc}"
    finally:
        store.close()


def test_the_blind_baseline_is_the_number_everything_is_measured_against(tmp_path):
    how, net = _net(tmp_path, "blind", VECTORS["blind"])
    assert how == "ran", net
    assert abs(net) < HONEST, f"a blind alpha should earn nothing much, got {net:.2%}"


@pytest.mark.parametrize("vector", [v for v in VECTORS if v != "blind"])
def test_no_route_to_the_future(tmp_path, vector):
    """Either the route is refused, or it earns what a blind alpha earns.

    Both are passes. What must never happen is a run that succeeds *and* earns what
    only a time machine can earn.
    """
    how, result = _net(tmp_path, vector.replace(".", "_").replace(" ", "_"), VECTORS[vector])
    if how == "refused":
        return
    assert abs(result) < LEAKING, (
        f"'{vector}' earned {result:.2%} against a blind ~3%. That is not an edge, "
        f"it is a step reading rows that had not happened yet"
    )


def test_reading_the_source_file_off_disk_is_the_known_boundary(tmp_path):
    """This one leaks, and is expected to.

    A step is arbitrary Python: it can open the file a source reads and take the
    whole history. Nothing in this process can prevent that, so it is recorded as
    the edge of what the as-of views can promise rather than left to be discovered
    again by somebody who thought they had found an alpha.
    """
    how, net = _net(tmp_path, "off_disk", 'full = pd.read_csv(ctx.root / "seed/bars.csv")')
    assert how == "ran"
    assert net > LEAKING, (
        "if this ever stops leaking, something now sandboxes a step -- delete this "
        "test and celebrate"
    )
