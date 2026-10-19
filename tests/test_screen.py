"""The screen: one date or many, and what each name did next.

These run against a small store built in the test, so the arithmetic is
checkable by eye. What the real corpus proves is in `examples/sec-10k`; what
this proves is the contract.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qanat.dates import WindowError, window
from qanat.models import Metric, Project, Returns, Stage
from qanat.screen import (
    Clause,
    Filter,
    ScreenError,
    forward_returns,
    parse_filters,
    screen,
)
from qanat.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(str(tmp_path / "q.duckdb"))
    s.write("raw.prices", pd.DataFrame({
        "subject": ["A", "A", "A", "B", "B", "B"],
        "as_of": pd.to_datetime(["2022-01-01", "2023-01-01", "2024-01-01"] * 2),
        "close": [10.0, 20.0, 30.0, 100.0, 50.0, 25.0],
        "pe": [5.0, 8.0, 40.0, 50.0, 9.0, 3.0],
    }), mode="replace")
    yield s
    s.close()


def a_project(with_returns: bool = True) -> Project:
    return Project(
        project="t", stages=[Stage(id="raw", kind="raw")],
        metrics=[Metric(id="pe", sql="SELECT subject, pe AS value FROM raw__prices "
                                     "WHERE as_of <= :as_of "
                                     "QUALIFY row_number() OVER (PARTITION BY subject "
                                     "ORDER BY as_of DESC) = 1")],
        returns=Returns(table="raw.prices") if with_returns else None,
    )


# -------------------------------------------------------------------- clauses
def test_a_clause_needs_a_comparison_that_exists():
    with pytest.raises(ScreenError, match="no comparison"):
        Clause(metric="pe", op="roughly", value=1)


def test_a_bare_list_of_clauses_is_one_unnamed_filter():
    """The common case should not need wrapping."""
    fs = parse_filters([{"metric": "pe", "op": "<", "value": 20}])
    assert len(fs) == 1 and fs[0].name is None and len(fs[0].clauses) == 1


def test_a_named_filter_keeps_its_name_and_version():
    fs = parse_filters([{"filter": "cheap", "version": 2,
                         "clauses": [{"metric": "pe", "op": "<", "value": 20}]}])
    assert (fs[0].name, fs[0].version) == ("cheap", 2)


def test_a_filter_records_itself_as_data_not_as_a_pointer():
    """A run keeps what ran. A pointer cannot record a clause nobody saved."""
    applied = parse_filters([{"metric": "pe", "op": "<", "value": 20}])[0].as_applied()
    assert applied == {"filter": None, "version": None,
                       "clauses": [{"metric": "pe", "op": "<", "value": 20.0}]}


def test_a_clause_missing_a_part_says_which_part():
    with pytest.raises(ScreenError, match="value"):
        parse_filters([{"metric": "pe", "op": "<"}])


# --------------------------------------------------------------------- dates
def test_one_date_is_a_window_of_one(store):
    """The whole point of one verb: the rows come back the same shape."""
    p = a_project()
    one = screen(store, p, parse_filters([{"metric": "pe", "op": "<", "value": 100}]),
                 as_of="2023-06-01")
    many = screen(store, p, parse_filters([{"metric": "pe", "op": "<", "value": 100}]),
                  frm="2023-06-01", to="2023-06-01", every="1y")
    assert [r["subject"] for r in one["rows"]] == [r["subject"] for r in many["rows"]]
    assert len(one["counts"]) == len(many["counts"]) == 1


def test_a_screen_needs_a_date(store):
    with pytest.raises(ScreenError, match="needs a date"):
        screen(store, a_project(), [])


def test_a_date_and_a_window_together_is_refused(store):
    with pytest.raises(ScreenError, match="either"):
        screen(store, a_project(), [], as_of="2023-01-01", frm="2022-01-01")


def test_a_window_counts_before_it_builds():
    """A year at one-second steps is 31.6M passes. Refusing beats building it."""
    with pytest.raises(WindowError, match="limit"):
        window("2020-01-01", "2021-01-01", "1m")


def test_a_backwards_window_is_refused():
    with pytest.raises(WindowError, match="before"):
        window("2025-01-01", "2024-01-01", "1y")


def test_a_year_is_a_unit():
    """An annual screener needs one, and the duration parser had no year in it."""
    assert len(window("2021-01-01", "2024-01-01", "1y")) == 4


# ------------------------------------------------------------------- the list
def test_the_screen_returns_who_passed(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 10}]), as_of="2023-06-01")
    passed = {r["subject"] for r in out["rows"] if r["passed"]}
    # At 2023-06-01 the latest rows are A pe=8 and B pe=9. Both under 10.
    assert passed == {"A", "B"}


def test_every_clause_has_to_pass(store):
    p = a_project()
    p.metrics.append(Metric(id="px", sql="SELECT subject, close AS value FROM raw__prices "
                                         "WHERE as_of <= :as_of QUALIFY row_number() OVER "
                                         "(PARTITION BY subject ORDER BY as_of DESC) = 1"))
    out = screen(store, p, parse_filters([{"metric": "pe", "op": "<", "value": 10},
                                          {"metric": "px", "op": ">", "value": 30}]),
                 as_of="2023-06-01")
    # A is pe 8 / price 20; B is pe 9 / price 50. Only B clears both.
    assert {r["subject"] for r in out["rows"] if r["passed"]} == {"B"}


def test_a_failing_row_says_which_clause_it_failed(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 8.5}]), as_of="2023-06-01")
    b = next(r for r in out["rows"] if r["subject"] == "B")
    assert b["passed"] is False and "pe < 8.5" in b["failed_on"]


def test_an_unknown_metric_names_the_ones_there_are(store):
    with pytest.raises(ScreenError, match="pe"):
        screen(store, a_project(), parse_filters(
            [{"metric": "nope", "op": "<", "value": 1}]), as_of="2023-06-01")


def test_a_metric_must_select_subject_and_value(store):
    p = a_project()
    p.metrics.append(Metric(id="bad", sql="SELECT 1 AS wrong"))
    with pytest.raises(ScreenError, match="subject"):
        screen(store, p, parse_filters([{"metric": "bad", "op": "<", "value": 1}]),
               as_of="2023-06-01")


# ------------------------------------------------------------------ the count
def test_a_subject_missing_a_metric_is_not_a_fail(store):
    """A list is a claim. A screen that silently dropped half the market has
    said something false about who passed, so the count travels with it."""
    store.write("raw.prices", pd.DataFrame({
        "subject": ["A", "C"], "as_of": pd.to_datetime(["2023-01-01"] * 2),
        "close": [10.0, 10.0], "pe": [5.0, None],
    }), mode="replace")
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 10}]), as_of="2023-06-01")
    c = out["counts"][0]
    assert c["subjects"] == 2 and c["evaluated"] == 1 and c["dropped"] == 1
    assert next(r for r in out["rows"] if r["subject"] == "C")["passed"] is None


# ----------------------------------------------------------------- the return
def test_the_return_runs_between_a_subjects_own_dates(store):
    """Companies file on different days, so there is no shared calendar."""
    fwd = forward_returns(store, a_project())
    a = fwd[fwd["subject"] == "A"].sort_values("as_of")
    assert a["forward_return"].iloc[0] == pytest.approx(1.0)    # 10 -> 20
    assert a["forward_return"].iloc[1] == pytest.approx(0.5)    # 20 -> 30
    assert pd.isna(a["forward_return"].iloc[2])                 # nothing after


def test_a_screened_row_carries_its_own_forward_return(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]), as_of="2023-06-01")
    a = next(r for r in out["rows"] if r["subject"] == "A")
    assert a["priced_on"] == "2023-01-01"
    assert a["forward_return"] == pytest.approx(0.5)


def test_no_returns_declared_is_still_a_screen(store):
    """A screen is a screen without a price. It just cannot say what came next."""
    out = screen(store, a_project(with_returns=False), parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]), as_of="2023-06-01")
    assert out["rows"] and "forward_return" not in out["rows"][0]


def test_returns_pointing_at_a_table_that_is_not_there_says_so(store):
    p = a_project()
    p.returns = Returns(table="raw.nope")
    with pytest.raises(ScreenError, match="nope"):
        screen(store, p, parse_filters([{"metric": "pe", "op": "<", "value": 1}]),
               as_of="2023-06-01")


# ------------------------------------------------------------- what is not here
def test_nothing_is_added_up(store):
    """No average across dates, no total, no equity curve. A number that cannot
    be read off one date's list is not in the result."""
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]),
        frm="2022-01-01", to="2024-01-01", every="1y")
    assert set(out) == {"rows", "counts", "applied"}
    for c in out["counts"]:
        assert set(c) == {"as_of", "subjects", "evaluated", "dropped", "passed"}


def test_a_screen_writes_no_table(store):
    before = set(store.all_tables())
    screen(store, a_project(), parse_filters([{"metric": "pe", "op": "<", "value": 100}]),
           frm="2022-01-01", to="2024-01-01", every="1y")
    assert set(store.all_tables()) == before


def test_the_same_screen_twice_is_the_same_answer(store):
    args = (store, a_project(), parse_filters([{"metric": "pe", "op": "<", "value": 10}]))
    assert screen(*args, as_of="2023-06-01") == screen(*args, as_of="2023-06-01")


def test_a_filter_object_can_be_passed_directly(store):
    out = screen(store, a_project(),
                 [Filter(name="cheap", version=1,
                         clauses=[Clause(metric="pe", op="lt", value=10)])],
                 as_of="2023-06-01")
    assert out["applied"][0]["filter"] == "cheap"
