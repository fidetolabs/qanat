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
    assert set(out) <= {"rows", "counts", "applied", "execution", "universe", "notes"}
    for c in out["counts"]:
        assert set(c) == {"as_of", "subjects", "evaluated", "dropped", "passed",
                          "nothing_evaluable"}
    # Every count is about its own date and nothing else. The guard this test
    # exists for: no mean, no total, no equity curve, nothing cumulative.
    for c in out["counts"]:
        assert c["subjects"] == c["evaluated"] + c["dropped"]
    assert not any(
        word in key
        for container in (out, *out["counts"], *out["rows"])
        for key in container
        for word in ("total", "mean", "avg", "cumulative", "curve", "sum", "since")
    )
    # A row's values are that subject's, on that date. `execution` is metadata
    # about the call and carries no number read off more than one date.
    wanted = {c["metric"] for f in out["applied"] for c in f["clauses"]}
    for r in out["rows"]:
        assert set(r["values"]) <= {m for m in wanted} | {
            f"{m}@{v}" for m in wanted for v in range(1, 10)}
    assert set(out["execution"]) <= {
        "id", "as_of", "from", "to", "every", "metrics", "dates", "corpus",
        "universe", "note"}


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


# ------------------------------------------------------------- the answerable window
def test_coverage_reports_the_range_and_what_sets_it(store):
    """So the first call can be right rather than quietly narrow."""
    from qanat.screen import coverage

    have = coverage(store, a_project())
    assert have["from"] == "2022-01-01"
    assert have["to"] == "2024-01-01"
    assert [t["ref"] for t in have["tables"]] == ["raw.prices"]
    assert have["tables"][0]["written"] is True
    assert "nothing to read" in have["why"]


def test_coverage_on_a_project_with_nothing_written_says_to_run_it(tmp_path):
    from qanat.screen import coverage

    s = Store(str(tmp_path / "empty.duckdb"))
    try:
        have = coverage(s, a_project())
        assert have["from"] is None
        assert "Run the pipeline" in have["why"]
    finally:
        s.close()


def test_a_window_starting_before_the_corpus_returns_the_covered_range(store):
    """Not a silently shorter list.

    A screen that quietly answered for three of the five years asked for has
    told the caller something false, which is the same reason `counts` exists.
    """
    with pytest.raises(ScreenError) as exc:
        screen(store, a_project(), parse_filters(
            [{"metric": "pe", "op": "<", "value": 100}]),
            frm="2020-01-01", to="2024-01-01", every="1y")
    said = str(exc.value)
    assert "2022-01-01 to 2024-01-01" in said, "it says what it does hold"
    assert "2020-01-01" in said, "and which date it could not answer for"
    assert "list of nobody" in said, "and why that is not an empty answer"
    assert "coverage" in said, "and what to call instead"


def test_a_single_date_before_the_corpus_is_refused_the_same_way(store):
    with pytest.raises(ScreenError, match="2022-01-01 to 2024-01-01"):
        screen(store, a_project(), parse_filters(
            [{"metric": "pe", "op": "<", "value": 100}]), as_of="2019-06-01")


def test_a_window_inside_the_coverage_is_answered(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]),
        frm="2022-06-01", to="2023-06-01", every="1y")
    assert [c["as_of"] for c in out["counts"]] == ["2022-06-01", "2023-06-01"]
    assert "notes" not in out, "a window it can answer for gets no caveat"


def test_a_date_past_the_newest_row_is_a_note_not_a_refusal(store):
    """Reading the same corpus as the newest date is flat, not false."""
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]), as_of="2030-01-01")
    assert out["rows"], "it still answers"
    assert any("past the newest row" in n for n in out["notes"])
    assert any("2024-01-01" in n for n in out["notes"])


def test_a_screen_over_a_window_gives_each_company_its_own_forward_return(store):
    """Per company, between its own dates. There is no shared calendar."""
    out = screen(store, a_project(), parse_filters(
        [{"filter": "anything", "clauses": [{"metric": "pe", "op": "<", "value": 100}]}]),
        frm="2022-06-01", to="2023-06-01", every="1y")

    assert out["applied"][0]["filter"] == "anything"
    by_date: dict[str, list[dict]] = {}
    for r in out["rows"]:
        by_date.setdefault(r["as_of"], []).append(r)
    assert set(by_date) == {"2022-06-01", "2023-06-01"}
    for date_rows in by_date.values():
        for r in date_rows:
            assert "forward_return" in r, "a survivor is priced from its own date"
            assert "priced_on" in r


def test_a_date_with_nothing_evaluable_says_so_rather_than_being_omitted(store):
    """Three zeros read as "nobody passed". That is a different answer."""
    project = a_project()
    project.metrics = [Metric(
        id="pe",
        sql="SELECT subject, pe AS value FROM raw__prices WHERE as_of <= :as_of "
            "AND as_of < DATE '2022-06-01' "
            "QUALIFY row_number() OVER (PARTITION BY subject ORDER BY as_of DESC) = 1")]
    out = screen(store, project, parse_filters(
        [{"metric": "pe", "op": "<", "value": 100}]),
        frm="2022-06-01", to="2023-06-01", every="1y")

    dates = [c["as_of"] for c in out["counts"]]
    assert dates == ["2022-06-01", "2023-06-01"], "no date is dropped from counts"
    for c in out["counts"]:
        assert "nothing_evaluable" in c
        assert c["nothing_evaluable"] is (c["evaluated"] == 0)


def test_counts_still_report_evaluated_and_dropped_per_date(store):
    project = a_project()
    project.metrics = [
        Metric(id="pe", sql="SELECT subject, pe AS value FROM raw__prices "
                            "WHERE as_of <= :as_of "
                            "QUALIFY row_number() OVER (PARTITION BY subject "
                            "ORDER BY as_of DESC) = 1"),
        # Only A has one, so B can never be judged.
        Metric(id="only_a", sql="SELECT subject, pe AS value FROM raw__prices "
                                "WHERE subject = 'A' AND as_of <= :as_of "
                                "QUALIFY row_number() OVER (PARTITION BY subject "
                                "ORDER BY as_of DESC) = 1"),
    ]
    out = screen(store, project, parse_filters([
        {"metric": "pe", "op": "<", "value": 100},
        {"metric": "only_a", "op": "<", "value": 100},
    ]), frm="2022-06-01", to="2023-06-01", every="1y")

    for c in out["counts"]:
        assert c["subjects"] == 2
        assert c["evaluated"] == 1
        assert c["dropped"] == 1, "B is not a fail, it is unjudgeable"
        assert c["nothing_evaluable"] is False


def test_the_coverage_tool_is_on_the_surface_and_writes_nothing():
    from qanat.mcp import TOOLS

    entry = next(t for t in TOOLS if t["name"] == "coverage")
    assert entry["writes"] is False
    assert entry["scope"] == "research", "the same rung as the screen it informs"


# ------------------------------------------- what the caller needs to keep a record
def test_a_screen_returns_the_values_behind_the_verdict(store):
    """A caller keeping its own history can only keep what it is handed.

    The split puts the filter, the history and the results on the caller's
    side. That only works if a screen returns the numbers the verdict was
    reached on -- a bare pass/fail would make the caller ask again for values
    this call has already computed.
    """
    # As of 2023-06-01 the newest row for each is 2023-01-01: A is 8.0, B is 9.0.
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 9}]), as_of="2023-06-01")
    by_subject = {r["subject"]: r for r in out["rows"]}

    assert by_subject["A"]["values"] == {"pe": 8.0}
    assert by_subject["B"]["values"] == {"pe": 9.0}
    assert by_subject["A"]["passed"] is True
    assert by_subject["B"]["passed"] is False
    # The losing side's number comes back too. A caller that kept only the
    # survivors could never say how close anything else was.
    assert by_subject["B"]["failed_on"] == "pe lt 9"


def test_the_values_are_keyed_so_two_versions_of_one_metric_stay_apart(store):
    project = a_project()
    project.metrics = [
        Metric(id="pe", version=1,
               sql="SELECT subject, pe AS value FROM raw__prices WHERE as_of <= :as_of "
                   "QUALIFY row_number() OVER (PARTITION BY subject "
                   "ORDER BY as_of DESC) = 1"),
        Metric(id="pe", version=2,
               sql="SELECT subject, pe * 10 AS value FROM raw__prices "
                   "WHERE as_of <= :as_of QUALIFY row_number() OVER "
                   "(PARTITION BY subject ORDER BY as_of DESC) = 1"),
    ]
    out = screen(store, project, parse_filters([{"filter": "both", "clauses": [
        {"metric": "pe", "metric_version": 1, "op": "lt", "value": 100},
        {"metric": "pe", "metric_version": 2, "op": "lt", "value": 100},
    ]}]), as_of="2023-06-01")
    values = {r["subject"]: r["values"] for r in out["rows"]}
    assert values["A"] == {"pe@1": 8.0, "pe@2": 80.0}
    assert values["B"] == {"pe@1": 9.0, "pe@2": 90.0}
    # And the record says both versions ran, which a mapping could not.
    assert out["execution"]["metrics"] == [
        {"metric": "pe", "version": 1}, {"metric": "pe", "version": 2}]


def test_a_subject_that_could_not_be_judged_says_so_in_its_values(store):
    """`None` rather than a missing key: the caller asked about this metric."""
    project = a_project()
    project.metrics = [Metric(
        id="pe", sql="SELECT subject, pe AS value FROM raw__prices "
                     "WHERE subject = 'A' AND as_of <= :as_of "
                     "QUALIFY row_number() OVER (PARTITION BY subject "
                     "ORDER BY as_of DESC) = 1")]
    out = screen(store, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]), as_of="2023-06-01")
    rows = {r["subject"]: r for r in out["rows"]}
    assert rows["A"]["values"] == {"pe": 8.0}
    assert rows["A"]["evaluated"] is True
    assert list(rows["A"]["values"]) == ["pe"], "only what the filter named"


def test_an_unpinned_clause_records_the_version_it_actually_read(store):
    """Otherwise a kept answer cannot say what computed it, and is not a record."""
    project = a_project()
    project.metrics = [
        Metric(id="pe", version=1, sql="SELECT subject, 1.0 AS value FROM raw__prices "
                                       "WHERE as_of <= :as_of GROUP BY subject"),
        Metric(id="pe", version=4, sql="SELECT subject, 2.0 AS value FROM raw__prices "
                                       "WHERE as_of <= :as_of GROUP BY subject"),
    ]
    out = screen(store, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]), as_of="2023-06-01")
    clause = out["applied"][0]["clauses"][0]
    assert "metric_version" not in clause, "the caller pinned nothing"
    assert clause["metric_version_used"] == 4, "and this is what ran"
    assert out["execution"]["metrics"] == [{"metric": "pe", "version": 4}]


def test_a_pinned_clause_records_both_what_was_asked_and_what_ran(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "metric_version": 1, "op": "lt", "value": 100}]),
        as_of="2023-06-01")
    clause = out["applied"][0]["clauses"][0]
    assert clause["metric_version"] == 1
    assert clause["metric_version_used"] == 1


def test_the_execution_id_is_a_digest_of_the_inputs_not_a_random_handle(store):
    """So two runs of the same thing match, and a different id means something
    underneath moved -- which an as-of date cannot tell you about a reparse."""
    args = (store, a_project(), parse_filters([{"metric": "pe", "op": "lt", "value": 10}]))
    first = screen(*args, as_of="2023-06-01")
    again = screen(*args, as_of="2023-06-01")
    assert first["execution"]["id"] == again["execution"]["id"]

    other_date = screen(*args, as_of="2022-06-01")
    assert other_date["execution"]["id"] != first["execution"]["id"]
    assert "digest" in first["execution"]["note"]


def test_the_execution_id_moves_when_the_definition_moves(store):
    """A filer restating is what as-of protects against. A parser being fixed
    and the corpus reloaded is not, and this is what shows it."""
    project = a_project()
    before = screen(store, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 10}]), as_of="2023-06-01")

    project.metrics = [*project.metrics, Metric(
        id="pe", version=2, sql="SELECT subject, pe * 2 AS value FROM raw__prices "
                                "WHERE as_of <= :as_of QUALIFY row_number() OVER "
                                "(PARTITION BY subject ORDER BY as_of DESC) = 1")]
    after = screen(store, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 10}]), as_of="2023-06-01")
    assert after["execution"]["id"] != before["execution"]["id"]
    assert after["execution"]["metrics"] == [{"metric": "pe", "version": 2}]


def test_the_execution_block_records_the_corpus_range_it_ran_over(store):
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 10}]), as_of="2023-06-01")
    assert out["execution"]["corpus"] == {"from": "2022-01-01", "to": "2024-01-01"}
    assert out["execution"]["dates"] == ["2023-06-01"]
    assert out["execution"]["as_of"] == "2023-06-01"


def test_there_is_no_wall_clock_in_the_answer(store):
    """The same screen twice is the same answer, which is a claim this product
    makes. When a caller asked is the caller's own fact to record."""
    args = (store, a_project(), parse_filters([{"metric": "pe", "op": "lt", "value": 10}]))
    assert screen(*args, as_of="2023-06-01") == screen(*args, as_of="2023-06-01")


# ----------------------------------------------------------------- the universe
def a_corpus_project(with_returns: bool = True) -> Project:
    """A project whose documents table is a company index."""
    from qanat.models import Corpus

    project = a_project(with_returns)
    project.corpus = Corpus(documents="raw.documents")
    return project


@pytest.fixture
def filers(store: Store) -> Store:
    """Two filers, addressable by ticker, matching the price fixture's subjects."""
    import json

    store.write("raw.documents", pd.DataFrame({
        "id": ["d1", "d2"], "entity": ["A", "B"],
        "doc_type": ["10-K", "10-K"],
        "published_at": pd.to_datetime(["2022-01-01", "2022-01-01"]),
        "source_keys": [json.dumps({"ticker": t, "company_name": t + " Co"})
                        for t in ("AYE", "BEE")],
    }), mode="replace")
    return store


def test_a_universe_is_a_list_and_only_a_list(store):
    """One form. An earlier version also took the name of a universe declared
    in the project file, which made this end look up a list belonging to the
    caller -- the same mistake as storing their filter."""
    with pytest.raises(ScreenError) as exc:
        screen(store, a_project(), parse_filters(
            [{"metric": "pe", "op": "lt", "value": 100}]),
            as_of="2023-06-01", universe="AYE")
    assert "list of names, not one name" in str(exc.value)
    assert "yours to keep" in str(exc.value)

    from qanat.mcp import TOOLS

    entry = next(x for x in TOOLS if x["name"] == "screen")
    assert entry["inputSchema"]["properties"]["universe"]["type"] == "array"


def test_a_universe_bounds_who_is_considered(filers):
    """Which names somebody cares about is theirs; placing them is not."""
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["AYE"])
    assert [r["subject"] for r in out["rows"]] == ["A"]
    assert out["counts"][0]["subjects"] == 1
    assert out["universe"] == {"asked": 1, "matched": 1, "unknown": [],
                               "point_in_time": False, "checked": True}


def test_a_universe_widens_the_answer_as_well_as_narrowing_it(filers):
    """A name with no value comes back unjudgeable, not absent.

    Without a universe the subjects are whoever the metrics returned, so there
    is no way to know who is missing -- and "nobody else qualified" is not the
    same answer as "I could not judge one of them".
    """
    project = a_corpus_project()
    project.metrics = [Metric(
        id="pe", sql="SELECT subject, pe AS value FROM raw__prices "
                     "WHERE subject = 'A' AND as_of <= :as_of "
                     "QUALIFY row_number() OVER (PARTITION BY subject "
                     "ORDER BY as_of DESC) = 1")]
    out = screen(filers, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["AYE", "BEE"])

    rows = {r["subject"]: r for r in out["rows"]}
    assert set(rows) == {"A", "B"}
    assert rows["B"]["evaluated"] is False
    assert rows["B"]["passed"] is None, "not a fail -- unjudgeable"
    assert rows["B"]["values"] == {"pe": None}
    assert out["counts"][0] == {"as_of": "2023-06-01", "subjects": 2, "evaluated": 1,
                                "dropped": 1, "passed": 1, "nothing_evaluable": False}


def test_a_ticker_is_placed_against_its_filer(filers):
    """The facts carry a CIK and a person writes a ticker."""
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["aye", "BEE"])
    assert sorted(r["subject"] for r in out["rows"]) == ["A", "B"]
    assert out["universe"]["unknown"] == []


def test_a_name_this_project_does_not_hold_is_reported_never_dropped(filers):
    """A list of fifty where ten are not held is a screen that quietly
    answered for forty."""
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["AYE", "NOTALISTEDTICKER"])
    assert out["universe"]["matched"] == 1
    assert out["universe"]["asked"] == 2
    assert out["universe"]["unknown"] == ["NOTALISTEDTICKER"]
    assert any("not in this project" in n for n in out["notes"])
    assert any("list_companies" in n for n in out["notes"])


def test_a_universe_this_project_holds_none_of_is_refused(filers):
    with pytest.raises(ScreenError) as exc:
        screen(filers, a_corpus_project(), parse_filters(
            [{"metric": "pe", "op": "lt", "value": 100}]),
            as_of="2023-06-01", universe=["NOPE", "ALSONOPE"])
    assert "holds none of those" in str(exc.value)
    assert "list_companies" in str(exc.value)


def test_a_project_with_no_company_index_says_the_names_were_not_checked(store):
    """An empty index is not evidence that a name is wrong.

    A subject is not required to be a filer -- the SEC corpus is the first
    dataset here, not the subject -- so with nothing to check against the name
    is taken as written and the answer says so.
    """
    out = screen(store, a_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["A", "B"])
    assert sorted(r["subject"] for r in out["rows"]) == ["A", "B"]
    assert out["universe"]["checked"] is False
    assert out["universe"]["unknown"] == []
    # Not a count of names it found -- it looked for none of them. A zero here
    # would read as "found nothing" and a 2 as "found both", and neither is true.
    assert out["universe"]["matched"] is None
    assert out["universe"]["asked"] == 2, "the names still went through"
    assert any("never checked is not a name that passed" in n for n in out["notes"])


def test_membership_is_point_in_time_when_the_list_carries_dates(filers):
    """B joins in 2023. A screen in 2022 must not return it.

    This is the survivorship bias stated plainly: without dates the list is
    today's membership applied to the past.
    """
    project = a_corpus_project()
    dated = [{"symbol": "AYE", "from": "2021-01-01"},
             {"symbol": "BEE", "from": "2023-03-01"}]

    early = screen(filers, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2022-06-01", universe=dated)
    assert [r["subject"] for r in early["rows"]] == ["A"], "B had not joined"
    assert early["universe"]["point_in_time"] is True

    late = screen(filers, project, parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=dated)
    assert sorted(r["subject"] for r in late["rows"]) == ["A", "B"]
    assert not any("flattered" in n for n in late.get("notes", []))


def test_a_member_that_has_left_is_gone_from_a_later_date(filers):
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01",
        universe=[{"symbol": "AYE"}, {"symbol": "BEE", "to": "2022-06-01"}])
    assert [r["subject"] for r in out["rows"]] == ["A"]


def test_a_list_with_no_dates_says_it_flatters_the_numbers(filers):
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["AYE", "BEE"])
    assert out["universe"]["point_in_time"] is False
    assert any("flattered by the names that survived" in n for n in out["notes"])


def test_the_universe_is_part_of_the_execution_id(filers):
    """The same filter over a different list is a different answer."""
    args = (filers, a_corpus_project(),
            parse_filters([{"metric": "pe", "op": "lt", "value": 100}]))
    both = screen(*args, as_of="2023-06-01", universe=["AYE", "BEE"])
    one = screen(*args, as_of="2023-06-01", universe=["AYE"])
    none = screen(*args, as_of="2023-06-01")

    assert both["execution"]["id"] != one["execution"]["id"]
    assert both["execution"]["id"] != none["execution"]["id"]
    assert none["execution"]["universe"] is None
    assert both["execution"]["universe"]["subjects"] == ["A", "B"]
    # And it is still reproducible.
    assert screen(*args, as_of="2023-06-01", universe=["AYE"]) == one


def test_a_universe_over_a_window_keeps_each_date_its_own(filers):
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        frm="2022-06-01", to="2023-06-01", every="1y", universe=["AYE", "BEE"])
    dates = [c["as_of"] for c in out["counts"]]
    assert dates == ["2022-06-01", "2023-06-01"]
    for c in out["counts"]:
        assert c["subjects"] == 2
    assert out["execution"]["dates"] == dates


def test_an_empty_universe_is_refused(store):
    with pytest.raises(ScreenError, match="at least one name"):
        screen(store, a_project(), parse_filters(
            [{"metric": "pe", "op": "lt", "value": 100}]),
            as_of="2023-06-01", universe=[])


def test_a_universe_entry_with_no_symbol_is_refused(store):
    with pytest.raises(ScreenError, match="needs a `symbol`"):
        screen(store, a_project(), parse_filters(
            [{"metric": "pe", "op": "lt", "value": 100}]),
            as_of="2023-06-01", universe=[{"from": "2021-01-01"}])


def test_the_screen_tool_takes_a_universe():
    from qanat.mcp import TOOLS

    entry = next(t for t in TOOLS if t["name"] == "screen")
    assert "universe" in entry["inputSchema"]["properties"]
    assert "universe" not in entry["inputSchema"]["required"], "it is optional"


def test_matched_and_unknown_account_for_every_name_asked_about(filers):
    """When a check happened, the three numbers have to add up."""
    out = screen(filers, a_corpus_project(), parse_filters(
        [{"metric": "pe", "op": "lt", "value": 100}]),
        as_of="2023-06-01", universe=["AYE", "BEE", "NOPE", "ALSONOPE"])
    u = out["universe"]
    assert u["checked"] is True
    assert u["asked"] == 4
    assert u["matched"] == 2
    assert len(u["unknown"]) == 2
    assert u["matched"] + len(u["unknown"]) == u["asked"]
