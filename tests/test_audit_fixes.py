"""The chaos audit, kept as tests.

Every case here is one finding from `audit/FINDINGS.md`, reduced to the smallest
thing that used to be wrong. The ids in the names are the ids in that file.

The point of the file is not coverage. It is that these particular mistakes were
each made once, shipped, and only found by going looking -- so each one gets a
test that fails loudly if it comes back.
"""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from qanat.models import Step
from qanat.store import Store


# ------------------------------------------------------- an alpha and the clock
def test_w01_store_read_respects_the_replay_clock(tmp_path: Path):
    """`ctx.store.read()` reached around the as-of views, needing no SQL at all."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.bars", pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-06-01"]), "close": [1.0, 2.0]}))
    s.open_pit("2024-02-01")
    try:
        assert len(s.read("raw.bars")) == 1, "a read during a replay must see the past only"
    finally:
        s.close_pit()
    assert len(s.read("raw.bars")) == 2
    s.close()


def test_w02_a_timezone_offset_is_not_thrown_away(tmp_path: Path):
    """Three spellings of one instant compared differently: a New York close was
    visible four hours before it happened."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.t", pd.DataFrame({
        "ts": ["2024-01-01T21:00:00Z", "2024-01-01 16:00:00-05:00", "2024-01-02 06:00:00+09:00"],
        "who": ["utc", "ny", "seoul"]}))
    assert len(s.read("raw.t", as_of="2024-01-01T20:00:00")) == 0
    assert len(s.read("raw.t", as_of="2024-01-01T21:00:00")) == 3
    s.close()


def test_w03_a_utc_stamp_is_visible_at_utc_now(tmp_path: Path):
    """`fetched_at` landed as TIMESTAMPTZ and rendered in the session zone, so off
    UTC every landed row was hidden from a replay for the length of the offset."""
    s = Store(tmp_path / "t.duckdb")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    s.write("raw.api", pd.DataFrame({"fetched_at": [now], "payload": ["{}"]}))
    assert len(s.read("raw.api", as_of=now.isoformat())) == 1
    s.close()


@pytest.mark.parametrize(
    ("col", "value", "expected"),
    [("time", 1704067200000, "2024-01-01"),   # epoch milliseconds -- USGS, Binance
     ("ts", 1704067200, "2024-01-01"),        # epoch seconds -- most of the rest
     ("year", 2024, "2024-01-01")],           # annual macro data
)
def test_r01_r04_an_integer_clock_is_read_by_magnitude(tmp_path, col, value, expected):
    """These raised `Unimplemented type for cast (BIGINT -> TIMESTAMP)` on every
    as-of read, or were not recognised as a clock at all."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.q", pd.DataFrame({col: [value], "v": [1]}))
    assert s.time_column("raw.q") == col
    assert str(s.max_time("raw.q")).startswith(expected)
    s.close()


# ------------------------------------------------------------ input validation
@pytest.mark.parametrize("ref", [
    'features.z" AS SELECT 1; DROP TABLE t; CREATE TABLE "y',   # a name that is SQL
    "features.Upper",                                            # unaddressable later
    "features.has space",
    "features.",
])
def test_f01_a_table_name_is_a_name(ref):
    """The table half of a reference was never validated, and it is interpolated
    into DDL: `qanat check` passed, then the step dropped a real table."""
    with pytest.raises(ValueError):
        Step(id="x", reads=[], writes=[ref], script="x.sql")


def test_u01_a_negative_cost_is_refused():
    """A negative commission pays you to trade, so turnover becomes profit: -9999
    bps put +1938% into the strategy book with nothing marking it."""
    from qanat.api import BacktestRequest

    with pytest.raises(ValueError):
        BacktestRequest(**{"from": "2024-01-01", "to": "2024-02-01", "fee_bps": -1})
    BacktestRequest(**{"from": "2024-01-01", "to": "2024-02-01", "fee_bps": 0})


def test_f12_an_absurd_replay_is_refused_before_it_is_built():
    """One year at `rebalance: 1s` is 31.6M passes: 49 seconds and 762 MB just to
    build the list of dates, before a single step ran."""
    from qanat.backtest import BacktestError, dates

    with pytest.raises(BacktestError, match="rebalances"):
        dates("2020-01-01", "2021-01-01", "1s")
    assert len(dates("2024-01-01", "2024-01-11", "1d")) == 11


def test_u07_a_duration_error_names_the_field_that_was_wrong():
    """The message said "retention must look like '7d'" whichever field you had
    actually mistyped, sending the reader to the wrong setting."""
    from qanat.backtest import BacktestError, dates

    with pytest.raises(BacktestError, match="rebalance must look like"):
        dates("2024-01-01", "2024-02-01", "banana")


# --------------------------------------------------------------- the arithmetic
def test_w04_a_holding_with_no_price_is_not_counted_as_held():
    """The price frame is a pivot, so a delisted name was still a column full of
    nulls: it stayed in the held set, earned zero, and raised no note."""
    from qanat.backtest import score_period
    from qanat.models import Backtest, Project, Stage

    project = Project(name="t", stages=[Stage(id="w", kind="weights")],
                      backtest=Backtest(prices="w.p"))
    prices = pd.DataFrame(
        {"A": [10.0, 11.0], "D": [10.0, None]},
        index=pd.to_datetime(["2024-01-02", "2024-01-03"]),
    )
    w = pd.Series({"A": 0.5, "D": 0.5})
    period, notes = score_period(prices, w, pd.Series(dtype=float),
                                 "2024-01-01", "2024-01-02", project)
    assert period.holdings == 1, "D has no exit price, so it was not held"
    assert any("no price" in n for n in notes)
    assert period.gross == pytest.approx(0.5 * 0.1)


def test_w16_decay_keeps_the_book_the_size_the_alpha_asked_for():
    """Averaging alone shrank a book whose sides offset -- |weights| 1.00 became
    0.33, so "less turnover" quietly also meant "a third of the bet"."""
    from qanat.backtest import decay_weights

    stops = ["a", "b", "c"]
    held = {"a": pd.Series({"X": 0.5, "Y": -0.5}),
            "b": pd.Series({"X": -0.5, "Y": 0.5}),
            "c": pd.Series({"X": 0.5, "Y": -0.5})}
    out = decay_weights(held, stops, 3)
    for stop in stops:
        assert float(out[stop].abs().sum()) == pytest.approx(1.0)


def test_w05_the_digest_covers_the_data(tmp_path: Path):
    """It hashed the code and the config and nothing else, so editing the numbers
    inside a source file left it unchanged -- and `compare` then told the reader
    the difference had to be the engine."""
    from qanat.backtest import data_fingerprint
    from qanat.models import Project, Source, Stage

    s = Store(tmp_path / "t.duckdb")
    project = Project(name="t", stages=[Stage(id="raw", kind="raw"), Stage(id="w", kind="weights")],
                      sources=[Source(id="b", writes=["raw.b"], connector="csv")])
    s.write("raw.b", pd.DataFrame({"date": ["2024-01-01"], "v": [1]}))
    before = data_fingerprint(s, project)
    s.write("raw.b", pd.DataFrame({"date": ["2024-01-01", "2024-01-02"], "v": [1, 2]}))
    assert data_fingerprint(s, project) != before
    s.close()


# ------------------------------------------------------------- landing the data
def test_w08_a_reordered_feed_does_not_write_into_the_wrong_columns(tmp_path: Path):
    """The key matched by name but the insert was positional, so a feed that
    reordered its columns put dates in the symbol column and reported ok."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.b", pd.DataFrame({"date": ["2024-01-01"], "symbol": ["A"], "close": [1.0]}))
    s.write("raw.b", pd.DataFrame({"symbol": ["B"], "date": ["2024-01-02"], "close": [2.0]}),
            mode="append")
    got = s.read("raw.b").sort_values("date")
    assert list(got["symbol"]) == ["A", "B"]
    assert list(got["close"]) == [1.0, 2.0]
    s.close()


def test_w10_a_null_in_a_key_does_not_defeat_dedup(tmp_path: Path):
    """`t.k = incoming.k` is never true for a null, so a row with a blank key part
    was appended again on every single poll, for ever."""
    s = Store(tmp_path / "t.duckdb")
    batch = pd.DataFrame({"date": ["2024-01-01", "2024-01-01"], "symbol": ["A", None], "v": [1, 2]})
    for _ in range(3):
        s.write("raw.n", batch, mode="append", key=["date", "symbol"])
    assert len(s.read("raw.n")) == 2
    s.close()


def test_r02_an_all_null_column_is_not_typed_by_a_guess(tmp_path: Path):
    """A field that is null in the first response became INTEGER, and the day it
    carried real data every poll failed on a cast."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.h", pd.DataFrame({"name": ["a"], "counties": [None]}))
    assert dict(s.table_info("raw.h").columns)["counties"] == "VARCHAR"
    s.write("raw.h", pd.DataFrame({"name": ["b"], "counties": [["US-MA"]]}), mode="append")
    assert s.table_info("raw.h").rows == 2
    s.close()


def test_w13_a_float_widens_the_column_instead_of_being_rounded(tmp_path: Path):
    """A first poll of whole numbers typed the column BIGINT, and 102.5 then
    silently became 102 -- a sub-dollar price became 0."""
    s = Store(tmp_path / "t.duckdb")
    s.write("raw.p", pd.DataFrame({"d": ["2024-01-01"], "close": [102]}))
    s.write("raw.p", pd.DataFrame({"d": ["2024-01-02"], "close": [102.5]}), mode="append")
    assert sorted(s.read("raw.p")["close"]) == [102.0, 102.5]
    s.close()


def test_w07_the_csv_connector_can_keep_a_zero_padded_ticker(tmp_path: Path):
    """`pd.read_csv` with no options turned KRX 005930 into 5930, and the universe
    file -- which lists it as text -- then joined to nothing."""
    from qanat.models import Source
    from qanat.sources import fetch

    (tmp_path / "t.csv").write_text("date,symbol\n2024-01-01,005930\n")
    src = Source(id="k", writes=["raw.k"], connector="csv",
                 options={"path": "./t.csv", "dtype": {"symbol": "str"}})
    assert list(fetch(src, tmp_path)["symbol"]) == ["005930"]

    bad = Source(id="k", writes=["raw.k"], connector="csv",
                 options={"path": "./t.csv", "dtypo": {}})
    with pytest.raises(ValueError, match="does not know the option"):
        fetch(bad, tmp_path)


def test_r05_r06_the_shapes_the_public_web_actually_returns():
    """A single object could not be landed at all, and `records:` had no way to
    reach element 1 of a `[metadata, rows]` body."""
    from qanat.sources.rest import _flatten, dig

    one = {"data": {"amount": "79235.53", "base": "BTC", "currency": "USD"}}
    assert len(_flatten(one, {"records": "data"})) == 1

    two = [{"page": 1}, [{"date": "2024", "value": 1}, {"date": "2023", "value": 2}]]
    assert len(dig(two, "1")) == 2

    with pytest.raises(KeyError, match="no step"):
        dig({"a": 1}, "nope")


def test_w15_an_unset_env_var_says_which_one():
    """It became an empty string, producing `Illegal header value b'Bearer '` or a
    request sent with a blank key -- and sometimes the actively wrong "rest needs
    options.url" when the option was set and the variable was not."""
    from qanat.models import Source
    from qanat.sources.rest import MissingEnv, _body

    src = Source(id="px", writes=["raw.p"], connector="rest",
                 options={"url": "https://example.invalid/${QANAT_NOT_SET_ANYWHERE}"})
    with pytest.raises(MissingEnv, match="QANAT_NOT_SET_ANYWHERE"):
        _body(src, None)


# ------------------------------------------------------------- the graph rules
def _project(tmp_path: Path, steps: str, files: dict) -> tuple:
    """The smallest legal project, plus whatever steps a test wants to break."""
    from qanat.project import load

    (tmp_path / "steps").mkdir(exist_ok=True)
    (tmp_path / "seed").mkdir(exist_ok=True)
    (tmp_path / "seed/b.csv").write_text("date,symbol,close\n2024-01-01,A,1.0\n")
    (tmp_path / "steps/norm.sql").write_text("SELECT * FROM raw__b")
    (tmp_path / "steps/alpha.py").write_text(
        "import pandas as pd\ndef run(ctx):\n    return pd.DataFrame("
        "{'symbol': ['A'], 'weight': [1.0], 'as_of': ['2024-01-01']})\n")
    for name, body in files.items():
        (tmp_path / "steps" / name).write_text(body)
    (tmp_path / "qanat.yaml").write_text(
        "project: t\nstore: ./data/q.duckdb\n"
        "stages:\n  - {id: raw, kind: raw}\n  - {id: f, kind: features}\n"
        "  - {id: weights, kind: weights}\n"
        "sources:\n  - {id: b, to: [raw.b], connector: csv, mode: replace, "
        "options: {path: ./seed/b.csv}}\n"
        "steps:\n"
        "  - {id: norm, from: [raw.b], to: [f.prices], script: steps/norm.sql}\n"
        f"{steps}"
        "  - {id: alpha_h, from: [f.prices], to: [weights.h], script: steps/alpha.py}\n"
    )
    return load(tmp_path)


def test_g01_a_loop_between_steps_is_refused(tmp_path: Path):
    """`order()` gave up on a cycle and ran what was left in file order. On a
    populated store every step reported ok and the values grew ten-fold per run --
    the answer depended on how many times you had run the pipeline."""
    from qanat.project import validate

    p, root = _project(
        tmp_path,
        "  - {id: fa, from: [f.b], to: [f.a], script: steps/fa.sql}\n"
        "  - {id: fb, from: [f.a], to: [f.b], script: steps/fb.sql}\n",
        {"fa.sql": "SELECT * FROM f__b", "fb.sql": "SELECT * FROM f__a"},
    )
    rep = validate(p, root)
    assert any("loop" in e for e in rep.errors), rep.errors


def test_g02_one_table_has_one_producer(tmp_path: Path):
    """Two steps could write one weights table, and the book then counted one
    portfolio as two alphas with identical net."""
    from qanat.project import validate

    p, root = _project(
        tmp_path, "  - {id: alpha_g, from: [f.prices], to: [weights.h], script: steps/alpha.py}\n", {}
    )
    rep = validate(p, root)
    assert any("one producer" in e for e in rep.errors), rep.errors


def test_g04_a_var_with_no_option_behind_it_is_refused(tmp_path: Path):
    """It stayed in the query as a string literal matching nothing, so a typo in a
    YAML key emptied the table and reported ok."""
    from qanat.project import validate

    p, root = _project(
        tmp_path,
        "  - {id: filt, from: [f.prices], to: [f.q], script: steps/f.sql, options: {other: A}}\n",
        {"f.sql": "SELECT * FROM f__prices WHERE symbol = '${sym}'"},
    )
    rep = validate(p, root)
    assert any("${sym}" in e for e in rep.errors), rep.errors


def test_f02_a_script_outside_the_project_is_refused(tmp_path: Path):
    """A step runs the file it names, and `../` was accepted -- so the console could
    write a .py file anywhere and then execute it."""
    from qanat.project import validate

    p, root = _project(tmp_path, "", {})
    (tmp_path.parent / "outside.py").write_text("def run(ctx):\n    return None\n")
    p.steps[-1].script = "../outside.py"
    rep = validate(p, root)
    assert any("outside the project" in e for e in rep.errors), rep.errors


def test_f07_a_rejected_edit_leaves_the_project_alone(tmp_path: Path):
    """The edit was applied and validated afterwards, so a rejected one stayed in
    memory: the console then reported errors about a step that is not in the file,
    and every later edit failed against it until the server was restarted."""
    from qanat.editor import EditorError, save_step
    from qanat.project import validate

    p, root = _project(tmp_path, "", {})
    with pytest.raises(EditorError):
        save_step(p, root, {"id": "bogus", "from": ["nope.nothing"], "to": ["f.b1"],
                            "script": "steps/alpha.py"}, create_script=False)
    assert validate(p, root).ok, "the rejected step must not still be in the project"
    assert "bogus" not in (root / "qanat.yaml").read_text()


def test_g03_a_step_whose_upstream_failed_is_skipped(tmp_path: Path):
    """It recomputed from the last good version, reported ok, and stamped itself as
    applied -- so one vendor failure left a store that looked green everywhere."""
    from qanat.runner import run_all
    from qanat.store import Store

    p, root = _project(
        tmp_path,
        "  - {id: fa, from: [f.prices], to: [f.a], script: steps/fa.py}\n"
        "  - {id: fb, from: [f.a], to: [f.b], script: steps/fb.sql}\n",
        {"fa.py": "def run(ctx):\n    raise RuntimeError('the vendor file was truncated')\n",
         "fb.sql": "SELECT * FROM f__a"},
    )
    store = Store(p.store_url(root))
    out = {r.job_id: r for r in run_all(store, p, root)}
    assert out["fa"].status == "failed"
    assert out["fb"].status == "skipped", "fb reads what fa did not refresh"
    store.close()
