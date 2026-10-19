"""Can a step reach data that had not happened yet?

The one question this tool has to answer correctly. Everything else is
arithmetic that can be checked by reading; leakage cannot, because a leaking
step looks like a brilliant one.

**The question is asked directly now.** It used to be asked through money: the
step held a basket, and one that could see two days ahead earned 1292% against
an honest 3%. That worked while the package priced a basket, and it only ever
caught a leak that happened to be profitable. Nothing prices a basket any
more, so each vector below reports the newest row it could reach and the test
compares that against the day the pass is pretending to be. A leak is a date
in the future whether or not it would have paid.

Every vector runs the same reaching logic and differs only in how it tries to
get at the prices.
"""

import pandas as pd
import pytest
from lab import data, harness

#: The day every pass pretends to be. The data runs well past it, so anything
#: reaching beyond is reaching into rows that had not happened yet.
AS_OF = "2024-03-01"

PROBE = '''import pandas as pd


def run(ctx):
    legit = ctx.read("normalized.prices")
    visible_max = legit["date"].max() if len(legit) else None
    {getter}
    reached = None
    if full is not None and len(full):
        reached = pd.to_datetime(full["date"]).max()
    return pd.DataFrame([{{
        "visible_max": visible_max,
        "reached": reached,
        "date": visible_max,
    }}])
'''

VECTORS = {
    "blind":          "full = None",
    "ctx.read":       "full = legit",
    "ctx.sql bare":   'full = ctx.sql("SELECT date, symbol, close FROM normalized__prices")',
    "ctx.sql raw":    'full = ctx.sql("SELECT date, symbol, close FROM raw__bars")',
    "ctx.sql main.":  'full = ctx.sql("SELECT date, symbol, close FROM main.raw__bars")',
    "ctx.store.read": 'full = ctx.store.read("raw.bars")',
}


def reach(tmp_path, name, getter):
    """The newest row this vector could get to, running as of AS_OF.

    Returns ("refused", why) when the engine stopped it, which is a pass, or
    ("ran", the newest date it reached).
    """
    bars = data.alternating_winner(data.calendar(200))
    root = harness.build(tmp_path, name, bars, PROBE.format(getter=getter))
    project, r, store, _rep, results = harness.run(root)
    try:
        if any(not x.ok for x in results):
            return "refused", next(x.error for x in results if not x.ok)
        passes = harness.at(store, project, r, AS_OF)
        if any(not x.ok for x in passes):
            return "refused", next(x.error for x in passes if not x.ok)
        probe = store.read("normalized.probe")
        if probe.empty or pd.isna(probe["reached"].iloc[0]):
            return "ran", None
        return "ran", pd.Timestamp(probe["reached"].iloc[0])
    except Exception as exc:                      # noqa: BLE001 -- refusing is a pass
        return "refused", f"{type(exc).__name__}: {exc}"
    finally:
        store.close()


@pytest.mark.audit
def test_the_blind_baseline_reaches_nothing(tmp_path):
    """The control. A step that does not try sees no rows at all through `full`."""
    how, reached = reach(tmp_path, "blind", VECTORS["blind"])
    assert how == "ran", reached
    assert reached is None


@pytest.mark.audit
@pytest.mark.parametrize("vector", [v for v in VECTORS if v != "blind"])
def test_no_route_to_the_future(tmp_path, vector):
    """Either the route is refused, or everything it reached is in the past.

    Both are passes. What must never happen is a pass that succeeds and comes
    back holding a row dated after the day it was pretending to be.
    """
    name = vector.replace(".", "_").replace(" ", "_")
    how, reached = reach(tmp_path, name, VECTORS[vector])
    if how == "refused" or reached is None:
        return
    assert reached <= pd.Timestamp(AS_OF), (
        f"'{vector}' reached {reached.date()} while the pass was pretending to be "
        f"{AS_OF}. That is a step reading rows that had not happened yet."
    )


@pytest.mark.audit
def test_reading_the_source_file_off_disk_is_the_known_boundary(tmp_path):
    """This one leaks, and is expected to.

    A step is arbitrary Python: it can open the file a source reads and take
    the whole history. Nothing in this process can prevent that, so it is
    recorded as the edge of what the as-of views can promise rather than left
    to be discovered again by somebody who thought they had found an edge.
    """
    how, reached = reach(tmp_path, "off_disk",
                         'full = pd.read_csv(ctx.root / "seed/bars.csv")')
    assert how == "ran", reached
    assert reached is not None and reached > pd.Timestamp(AS_OF), (
        "if this ever stops leaking, something now sandboxes a step -- delete "
        "this test and celebrate"
    )
