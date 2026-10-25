"""The reading surface, on a two-document store small enough to check by eye."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qanat.models import Corpus, Project, Stage
from qanat.reading import (
    ReadingError,
    check_number,
    list_sections,
    read_facts,
    read_section,
    search_documents,
)
from qanat.store import Store

LONG = "risk " * 2000  # 10,000 characters, so pagination has something to do


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(str(tmp_path / "q.duckdb"))
    s.write("raw.documents", pd.DataFrame({
        "id": ["D1", "D2"], "entity": ["0001", "0002"],
        "published_at": pd.to_datetime(["2025-02-01", "2025-03-01"]),
        "period_end": pd.to_datetime(["2024-12-31", "2024-12-31"]),
    }), mode="replace")
    s.write("normalized.spans", pd.DataFrame({
        "document": ["D1", "D1", "D2"],
        "section": ["Item 1", "Item 1A", "Item 1"],
        "char_start": [0, 100, 0], "char_end": [100, 10_100, 40],
        "n_chars": [100, 10_000, 40], "rule": ["ordered"] * 3,
        "text": ["Item 1. Business We sell widgets to everyone.", LONG,
                 "Item 1. Business We sell gadgets."],
        # The filing date, as `split_sections` stamps it. A spans table without
        # this has no clock, so the as-of views do not filter it at all.
        "as_of": pd.to_datetime(["2025-02-01", "2025-02-01", "2025-03-01"]),
    }), mode="replace")
    s.write("normalized.facts", pd.DataFrame({
        "document": ["D1", "D1", "D2"],
        "concept": ["us-gaap:Revenues", "us-gaap:NetIncomeLoss", "us-gaap:Revenues"],
        "dims": ["{}"] * 3,
        "period_start": pd.to_datetime(["2024-01-01"] * 3),
        "period_end": pd.to_datetime(["2024-12-31"] * 3),
        "is_instant": [False] * 3,
        "value": [1_000.0, 120.0, 50.0], "unit": ["iso4217:USD"] * 3,
        "decimals": ["-6"] * 3, "as_of": pd.to_datetime(["2025-02-01"] * 3),
    }), mode="replace")
    yield s
    s.close()


def a_project(with_corpus: bool = True) -> Project:
    return Project(project="t", stages=[Stage(id="raw", kind="raw")],
                   corpus=Corpus(documents="raw.documents", spans="normalized.spans",
                                 facts="normalized.facts") if with_corpus else None)


# ------------------------------------------------------------------- the map
def test_list_sections_reports_length_before_you_read(store):
    """So the agent chooses knowing what a section will cost."""
    out = list_sections(store, a_project(), "D1")
    by = {s["section"]: s for s in out["sections"]}
    assert by["Item 1A"]["chars"] == 10_000
    assert by["Item 1A"]["pages"] == 3


def test_list_sections_says_which_rule_fired(store):
    """Detection is a heuristic. A caller that can read `rule` can distrust it."""
    assert all(s["rule"] for s in list_sections(store, a_project(), "D1")["sections"])


def test_a_document_with_no_sections_says_so(store):
    with pytest.raises(ReadingError, match="no sections"):
        list_sections(store, a_project(), "nope")


# ------------------------------------------------------------------ the text
def test_read_section_never_returns_the_whole_thing(store):
    """A tool that hands an agent 100 KB has spent the context it needed."""
    page = read_section(store, a_project(), "D1", "Item 1A")
    assert len(page["text"]) == 4000 and page["pages"] == 3


def test_a_later_page_continues_where_the_last_one_stopped(store):
    p1 = read_section(store, a_project(), "D1", "Item 1A", page=1)
    p2 = read_section(store, a_project(), "D1", "Item 1A", page=2)
    assert p1["chars"][1] == p2["chars"][0]


def test_a_page_says_where_in_the_document_it_came_from(store):
    """Item 1A starts at 100 in the document, so page 1 does too."""
    assert read_section(store, a_project(), "D1", "Item 1A")["document_chars"][0] == 100


def test_asking_past_the_end_says_how_many_pages_there_are(store):
    with pytest.raises(ReadingError, match="page 9"):
        read_section(store, a_project(), "D1", "Item 1A", page=9)


def test_an_unknown_section_lists_the_ones_there_are(store):
    with pytest.raises(ReadingError, match="Item 1A"):
        read_section(store, a_project(), "D1", "Item 9Z")


# ---------------------------------------------------------------- the search
def test_search_returns_locations_and_never_bodies(store):
    """Find, then read. A search that returns text is a second read_section
    with no page limit on it."""
    out = search_documents(store, a_project(), "widgets")
    assert out["matches"] and all("text" not in m for m in out["matches"])
    assert out["matches"][0]["first_at"] >= 0


def test_search_can_be_scoped_to_one_section(store):
    assert not search_documents(store, a_project(), "widgets", section="Item 1A")["matches"]
    assert search_documents(store, a_project(), "widgets", section="Item 1")["matches"]


def test_search_can_be_scoped_to_one_entity(store):
    out = search_documents(store, a_project(), "sell", entity="0002")
    assert [m["document"] for m in out["matches"]] == ["D2"]


def test_an_empty_query_is_refused(store):
    with pytest.raises(ReadingError, match="look for"):
        search_documents(store, a_project(), "   ")


# ----------------------------------------------------------------- the facts
def test_read_facts_returns_both_period_dates_and_the_as_of(store):
    row = read_facts(store, a_project(), "D1")["facts"][0]
    assert {"period_start", "period_end", "as_of", "dims", "unit"} <= set(row)


def test_read_facts_can_be_narrowed_to_one_concept(store):
    out = read_facts(store, a_project(), "D1", concept="Revenues")
    assert out["total"] == 1 and out["facts"][0]["value"] == 1000.0


def test_read_facts_says_how_many_it_held_back(store):
    out = read_facts(store, a_project(), "D1", limit=1)
    assert out["total"] == 2 and out["returned"] == 1


# ------------------------------------------------------------ check_number
def test_check_number_finds_the_tagged_value_near_a_prose_one(store):
    out = check_number(store, a_project(), "D1", value=1_010.0, within=0.05)
    assert out["candidates"][0]["value"] == 1000.0
    assert out["candidates"][0]["difference"] == pytest.approx(-10.0)


def test_check_number_refuses_to_say_which_is_right(store):
    """Two things produce a gap and the tool can tell neither from the other."""
    out = check_number(store, a_project(), "D1", value=1_010.0)
    assert "cannot tell them apart" in out["note"]
    assert not any(k in out for k in ("correct", "verdict", "matches"))


def test_a_number_nothing_is_near_comes_back_empty_rather_than_wrong(store):
    assert check_number(store, a_project(), "D1", value=9e9, within=0.01)["candidates"] == []


# ------------------------------------------------------------- the wiring
def test_a_project_with_no_corpus_says_what_to_add(store):
    with pytest.raises(ReadingError, match="corpus"):
        list_sections(store, a_project(with_corpus=False), "D1")


def test_a_corpus_pointing_at_nothing_says_to_run_the_pipeline(store):
    p = a_project()
    p.corpus.spans = "normalized.nope"
    with pytest.raises(ReadingError, match="Run the pipeline"):
        list_sections(store, p, "D1")


# ------------------------------------------------- the rows it actually reads
def test_no_reading_tool_loads_a_whole_table(store):
    """The regression this file exists to hold shut.

    Every tool here used to call `store.read()`, which is `SELECT *`, and then
    filter in pandas -- so opening one section of one filing read every section
    of every filing first. At 40 filings that is 11 ms; at 31,500 it is 1,101 ms
    against under 1 ms filtered.

    Asserted by taking the wide read away rather than by timing it. A timing test
    passes on a small fixture no matter what the code does, and this fixture is
    three spans.
    """
    proj = a_project()

    def refuse(*a, **kw):
        raise AssertionError("a reading tool called store.read(), which is SELECT *")

    store.read, kept = refuse, store.read
    try:
        assert list_sections(store, proj, "D1")["sections"]
        assert read_section(store, proj, "D1", "Item 1A")["text"]
        assert search_documents(store, proj, "widgets")["matches"]
        assert read_facts(store, proj, "D1")["facts"]
        assert check_number(store, proj, "D1", 1_000.0)["candidates"]
        # The error paths read too, and they are the easiest ones to leave wide.
        with pytest.raises(ReadingError):
            read_section(store, proj, "D1", "Item 99")
        with pytest.raises(ReadingError):
            list_sections(store, proj, "nope")
    finally:
        store.read = kept


def test_list_sections_does_not_carry_the_text(store):
    """`text` is nearly every byte in a spans table, and this tool reports
    lengths. It asked for all of it to return none of it."""
    proj = a_project()
    got = store.rows("normalized.spans",
                     columns="section, n_chars, char_start, char_end, rule",
                     where="document = ?", params=["D1"])
    assert "text" not in got.columns
    assert list_sections(store, proj, "D1")["sections"][0]["chars"] == 100


def test_a_filtered_read_still_cannot_see_past_the_as_of_date(store):
    """The as-of views are the whole point-in-time story, and a predicate is a
    new way to reach around them: `rows()` has to read through the view a screen
    opened, not the table under it.

    D1 was published 2025-02-01 and D2 on 2025-03-01, so the two dates below are
    either side of D2.
    """
    proj = a_project()
    # Two spans say "Business": D1's Item 1 and D2's. D1's Item 1A is the long
    # one, and it says "risk".
    assert len(search_documents(store, proj, "Business")["matches"]) == 2

    store.open_pit("2025-02-15")
    try:
        early = search_documents(store, proj, "Business")["matches"]
        assert {m["document"] for m in early} == {"D1"}, "D2 is from the future here"
        with pytest.raises(ReadingError):
            list_sections(store, proj, "D2")
    finally:
        store.close_pit()

    store.open_pit("2025-03-15")
    try:
        late = search_documents(store, proj, "Business")["matches"]
        assert {m["document"] for m in late} == {"D1", "D2"}
        assert list_sections(store, proj, "D2")["sections"]
    finally:
        store.close_pit()


def test_an_id_carrying_a_quote_stays_an_id(store):
    """A document id is caller input and the caller is an agent. It is bound,
    not pasted, so this is a miss rather than a syntax error or worse."""
    proj = a_project()
    for nasty in ("D1' OR '1'='1", "'; DROP TABLE normalized__spans; --"):
        with pytest.raises(ReadingError):
            list_sections(store, proj, nasty)
    assert list_sections(store, proj, "D1")["sections"], "the table is still there"
