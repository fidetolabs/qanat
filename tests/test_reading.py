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


# ------------------------------------------------------------- the company
@pytest.fixture
def rich(tmp_path: Path) -> Store:
    """A corpus with what the SEC connector actually lands: a form, and the
    ticker and company name inside `source_keys`."""
    import json

    s = Store(str(tmp_path / "rich.duckdb"))
    s.write("raw.documents", pd.DataFrame({
        "id": ["A25", "A24", "A25Q"],
        "entity": ["0000320193"] * 3,
        "doc_type": ["10-K", "10-K", "10-Q"],
        "published_at": pd.to_datetime(["2025-11-01", "2024-11-01", "2025-08-01"]),
        "period_end": pd.to_datetime(["2025-09-27", "2024-09-28", "2025-06-28"]),
        "source_keys": [json.dumps({"ticker": "AAPL", "company_name": "Apple Inc.",
                                    "cik": "0000320193"})] * 3,
    }), mode="replace")
    s.write("normalized.spans", pd.DataFrame({
        "document": ["A25", "A25", "A24"],
        "section": ["Item 1", "Item 7", "Item 1"],
        "char_start": [0, 500, 0], "char_end": [500, 900, 300],
        "n_chars": [500, 400, 300], "rule": ["ordered", "title", "ordered"],
        "text": ["Item 1. Business " + "x" * 480,
                 "Item 7. Management's Discussion Revenue was $391,035 million.",
                 "Item 1. Business " + "y" * 280],
        "as_of": pd.to_datetime(["2025-11-01", "2025-11-01", "2024-11-01"]),
    }), mode="replace")
    s.write("normalized.facts", pd.DataFrame({
        "document": ["A25", "A25", "A24"],
        "concept": ["us-gaap:Revenues", "dei:EntityPublicFloat", "us-gaap:Revenues"],
        "dims": ["{}"] * 3,
        "period_start": pd.to_datetime(["2024-09-29", "2025-09-27", "2023-10-01"]),
        "period_end": pd.to_datetime(["2025-09-27", "2025-09-27", "2024-09-28"]),
        "is_instant": [False, True, False],
        "value": [391_035e6, 3.2e12, 383_285e6],
        "unit": ["iso4217:USD"] * 3, "decimals": ["-6"] * 3,
        "as_of": pd.to_datetime(["2025-11-01", "2025-11-01", "2024-11-01"]),
    }), mode="replace")
    yield s
    s.close()


def test_a_ticker_resolves_to_a_filer(rich):
    """The facts carry a CIK, and the thing a person says is a name."""
    from qanat.reading import resolve_filer

    who = resolve_filer(rich, a_project(), "AAPL")
    assert who["cik"] == "0000320193"
    assert who["ticker"] == "AAPL"
    assert who["name"] == "Apple Inc."
    assert who["filings"] == 3
    assert who["resolved_from"] == "AAPL"


def test_a_screens_output_can_be_handed_straight_back_in(rich):
    """A screen returns the padded CIK. So does `subject` on a fact."""
    from qanat.reading import resolve_filer

    assert resolve_filer(rich, a_project(), "0000320193")["cik"] == "0000320193"
    # And an unpadded one, because a person types the short form.
    assert resolve_filer(rich, a_project(), "320193")["cik"] == "0000320193"
    assert resolve_filer(rich, a_project(), "aapl")["cik"] == "0000320193"


def test_an_unknown_filer_names_who_the_project_holds(rich):
    from qanat.reading import resolve_filer

    with pytest.raises(ReadingError) as exc:
        resolve_filer(rich, a_project(), "NOTAFILER")
    assert "AAPL" in str(exc.value)
    assert "find_filings" in str(exc.value), "and what to call to add one"


def test_read_company_is_the_way_in_and_returns_no_bodies(rich):
    """Sections with lengths and the rule that found each, and the concepts."""
    from qanat.reading import read_company

    out = read_company(rich, a_project(), "AAPL")
    assert out["filer"]["cik"] == "0000320193"
    assert out["returned"] == 3
    assert [f["document"] for f in out["filings"]] == ["A25", "A25Q", "A24"]

    newest = out["filings"][0]
    assert newest["form"] == "10-K"
    assert newest["period_end"] == "2025-09-27"
    sections = {s["section"]: s for s in newest["sections"]}
    assert set(sections) == {"Item 1", "Item 7"}
    assert sections["Item 7"]["chars"] == 400
    assert sections["Item 7"]["rule"] == "title", "the rule travels with the answer"
    assert newest["facts"]["total"] == 2
    assert newest["facts"]["taxonomies"] == {"us-gaap": 1, "dei": 1}
    assert "us-gaap:Revenues" in newest["facts"]["concepts"]

    # No bodies, anywhere in the answer.
    import json as _json

    blob = _json.dumps(out, default=str)
    assert "Management's Discussion Revenue was" not in blob
    assert "xxxx" not in blob
    for f in out["filings"]:
        for s in f["sections"]:
            assert "text" not in s and "body" not in s


def test_the_answer_does_not_grow_with_the_length_of_the_filings(tmp_path):
    """The promise that makes it an index rather than a second read_section."""
    import json as _json

    from qanat.reading import read_company

    def corpus(size: int) -> Store:
        s = Store(str(tmp_path / f"q{size}.duckdb"))
        s.write("raw.documents", pd.DataFrame({
            "id": ["D"], "entity": ["0000000001"], "doc_type": ["10-K"],
            "published_at": pd.to_datetime(["2025-01-01"]),
            "period_end": pd.to_datetime(["2024-12-31"]),
            "source_keys": [_json.dumps({"ticker": "X", "company_name": "X Co"})],
        }), mode="replace")
        s.write("normalized.spans", pd.DataFrame({
            "document": ["D"], "section": ["Item 1"],
            "char_start": [0], "char_end": [size], "n_chars": [size],
            "rule": ["ordered"], "text": ["z" * size],
            "as_of": pd.to_datetime(["2025-01-01"]),
        }), mode="replace")
        return s

    small, large = corpus(1_000), corpus(500_000)
    try:
        a = _json.dumps(read_company(small, a_project(), "X"), default=str)
        b = _json.dumps(read_company(large, a_project(), "X"), default=str)
        # The only difference is the digits in the two length fields.
        assert abs(len(b) - len(a)) < 40, f"{len(a)} -> {len(b)}"
    finally:
        small.close()
        large.close()


def test_read_company_narrows_by_form_and_by_period(rich):
    from qanat.reading import read_company

    only_q = read_company(rich, a_project(), "AAPL", form="10-Q")
    assert [f["document"] for f in only_q["filings"]] == ["A25Q"]

    in_2025 = read_company(rich, a_project(), "AAPL", frm="2025-01-01")
    assert sorted(f["document"] for f in in_2025["filings"]) == ["A25", "A25Q"]

    none = read_company(rich, a_project(), "AAPL", frm="2030-01-01")
    assert none["returned"] == 0, "an empty answer is an answer"


def test_the_concept_list_can_be_filtered_and_is_capped(rich):
    from qanat.reading import read_company

    out = read_company(rich, a_project(), "AAPL", concept="Revenues")
    newest = out["filings"][0]
    assert newest["facts"]["concepts"] == ["us-gaap:Revenues"]
    assert newest["facts"]["taxonomies"] == {"us-gaap": 1}

    capped = read_company(rich, a_project(), "AAPL", concepts=1)
    assert len(capped["filings"][0]["facts"]["concepts"]) == 1
    assert "2 concepts" in capped["filings"][0]["facts"]["concepts_note"]


def test_a_number_can_be_traced_to_its_sentence_in_one_call_chain(rich):
    """A6, start to finish, for a named filer.

    company -> its filings -> the sections of one, with the rule that found
    each boundary -> the facts -> the prose the number sits in.
    """
    from qanat.reading import read_company, read_facts, read_section

    project = a_project()
    company = read_company(rich, project, "AAPL")
    document = company["filings"][0]["document"]

    facts = read_facts(rich, project, document, concept="Revenues")
    revenue = facts["facts"][0]["value"]
    assert revenue == 391_035e6

    # The span the number is discussed in, and the rule that found its boundary.
    md_and_a = next(s for s in company["filings"][0]["sections"] if s["section"] == "Item 7")
    assert md_and_a["rule"] == "title"

    page = read_section(rich, project, document, "Item 7")
    assert "391,035" in page["text"], "the sentence the number came from"
    assert page["rule"] == md_and_a["rule"], "and the two agree about the rule"
    assert page["document_chars"][0] == md_and_a["char_start"]


def test_check_number_still_says_nothing_about_which_side_is_right(rich):
    out = check_number(rich, a_project(), "A25", 391_000e6, within=0.01)
    assert out["candidates"], "it found the tagged number"
    assert "cannot tell them apart" in out["note"]
    for row in out["candidates"]:
        assert "difference" in row
        assert "correct" not in row and "right" not in row


def test_a_corpus_with_no_source_keys_still_answers_by_cik(store):
    """A documents table built by something other than the SEC connector."""
    from qanat.reading import read_company, resolve_filer

    who = resolve_filer(store, a_project(), "0001")
    assert who["cik"] == "0001"
    assert who["ticker"] is None

    out = read_company(store, a_project(), "0001")
    assert [f["document"] for f in out["filings"]] == ["D1"]
    assert out["filings"][0]["form"] is None, "it does not invent one"
    assert {s["section"] for s in out["filings"][0]["sections"]} == {"Item 1", "Item 1A"}

    with pytest.raises(ReadingError, match="no `source_keys`"):
        resolve_filer(store, a_project(), "AAPL")


def test_read_company_needs_a_corpus_to_read(store):
    from qanat.reading import read_company

    with pytest.raises(ReadingError, match="documents"):
        read_company(store, a_project(with_corpus=False), "0001")


# --------------------------------------------------------- the company index
def test_list_companies_is_the_index_nothing_else_offered(rich):
    """`read_company` takes a filer and tells you about it. The only listing
    anywhere was buried in the error you got for naming one it did not have --
    so a caller building a list to screen could not find out which names land."""
    from qanat.reading import list_companies

    out = list_companies(rich, a_project())
    assert out["total"] == 1
    assert out["returned"] == 1
    one = out["companies"][0]
    assert one == {"cik": "0000320193", "ticker": "AAPL", "name": "Apple Inc.",
                   "filings": 3, "forms": ["10-K", "10-Q"],
                   "first_filed": "2024-11-01", "last_filed": "2025-11-01"}
    assert out["searchable_by_name"] is True
    assert "universe" in out["next"], "it says what it is for"


def test_list_companies_searches_by_ticker_or_name(rich):
    from qanat.reading import list_companies

    assert list_companies(rich, a_project(), query="AAPL")["returned"] == 1
    assert list_companies(rich, a_project(), query="apple")["returned"] == 1
    assert list_companies(rich, a_project(), query="pple in")["returned"] == 1
    assert list_companies(rich, a_project(), query="tesla")["returned"] == 0


def test_list_companies_on_a_corpus_with_no_names_says_so(store):
    """A documents table built by something other than the SEC connector."""
    from qanat.reading import list_companies

    out = list_companies(store, a_project())
    assert out["searchable_by_name"] is False
    assert {c["cik"] for c in out["companies"]} == {"0001", "0002"}
    assert all(c["ticker"] is None for c in out["companies"])
    with pytest.raises(ReadingError, match="no name to search"):
        list_companies(store, a_project(), query="AAPL")


def test_list_companies_needs_a_corpus(store):
    from qanat.reading import list_companies

    with pytest.raises(ReadingError, match="documents"):
        list_companies(store, a_project(with_corpus=False))


def test_the_index_places_a_ticker_a_padded_cik_and_a_bare_one(rich):
    from qanat.reading import company_index

    index = company_index(rich, a_project())
    assert index.known is True
    assert index.filers == frozenset({"0000320193"})
    for name in ("AAPL", "aapl", "0000320193", "320193"):
        assert index.place(name) == "0000320193", name
    assert index.place("TSLA") is None


def test_an_index_is_empty_and_says_so_when_there_is_nothing_to_build_from(store):
    """`known` is the field a caller must read before calling a name wrong."""
    from qanat.reading import company_index

    empty = company_index(store, a_project(with_corpus=False))
    assert empty.known is False
    assert empty.place("AAPL") is None, "and it places nothing"

    # A corpus with no tickers still knows its filers.
    by_cik = company_index(store, a_project())
    assert by_cik.known is True
    assert by_cik.place("0001") == "0001"
    assert by_cik.place("AAPL") is None


def test_list_companies_is_on_the_data_scope_and_writes_nothing():
    from qanat.mcp import TOOLS, tools_for

    entry = next(t for t in TOOLS if t["name"] == "list_companies")
    assert entry["writes"] is False
    assert entry["scope"] == "data"
    assert "list_companies" in {t["name"] for t in tools_for("data")}
