"""Section detection, on the shapes that actually broke it.

Every case here is a real one taken from the forty filings in
`examples/sec-10k`, reduced to the few hundred characters that mattered. They
are kept because each one was found by a document rather than imagined, and
each would otherwise come back.
"""

from __future__ import annotations

import pytest

from qanat.sections import FORM_10K, find_boundaries, normalise, split


def sections(text: str) -> list[str]:
    return [b.section for b in find_boundaries(normalise(text), "10-K")]


def test_a_plain_run_of_headings():
    text = ("Item 1. Business We make things. "
            "Item 1A. Risk Factors Many things could go wrong. "
            "Item 2. Properties We rent offices.")
    assert sections(text) == ["Item 1", "Item 1A", "Item 2"]


def test_spans_run_to_the_next_heading():
    text = "Item 1. Business We make things. Item 1A. Risk Factors It could go wrong."
    spans = split(text, "10-K")
    assert spans[0].text.strip() == "Item 1. Business We make things."
    assert spans[1].start == spans[0].end
    assert spans[-1].end == len(normalise(text))


def test_every_span_starts_with_its_own_heading():
    text = "Item 1. Business A. Item 1A. Risk Factors B. Item 2. Properties C."
    for s in split(text, "10-K"):
        assert s.text.lower().replace(" ", "").startswith(s.section.lower().replace(" ", ""))


# --------------------------------------------------------------- the contents page
def test_the_contents_page_is_not_the_document():
    """Every Item name packed into one run, each with a page number after it.
    A naive search finds this first and calls the whole filing Item 1."""
    toc = "".join(f"Item {k}.Title{i + 3}" for i, k in enumerate(FORM_10K))
    text = toc + " Item 1. Business The real one starts here."
    found = find_boundaries(normalise(text), "10-K")
    assert [b.section for b in found] == ["Item 1"]
    assert found[0].start > len(toc) - 10, "the boundary landed inside the contents page"


def test_part_three_is_not_mistaken_for_a_contents_page():
    """Items 10 to 14 are genuine headings whose bodies are one sentence each,
    so they are dense too. Density alone would throw them away."""
    text = " ".join(
        f"Item {n}. {title} The information required by this Item is incorporated "
        "by reference to our Proxy Statement."
        for n, title in [
            ("10", "Directors, Executive Officers and Corporate Governance"),
            ("11", "Executive Compensation"),
            ("12", "Security Ownership of Certain Beneficial Owners and Management"),
            ("13", "Certain Relationships and Related Transactions"),
            ("14", "Principal Accountant Fees and Services"),
        ]
    )
    assert sections(text) == ["Item 10", "Item 11", "Item 12", "Item 13", "Item 14"]


# ---------------------------------------------------------------- cross-references
def test_a_cross_reference_is_not_a_heading():
    text = ("Item 1. Business See the discussion in Item 1A of this Form 10-K. "
            "Item 1A. Risk Factors The real section.")
    found = find_boundaries(normalise(text), "10-K")
    assert [b.section for b in found] == ["Item 1", "Item 1A"]
    assert found[1].start > text.index("The real section") - 40


def test_a_quoted_reference_carrying_the_real_title_is_still_a_reference():
    """Costco: `the factors set forth in the section titled "Item 1A-Risk
    Factors"`. The title is right there, so only the quoting gives it away."""
    text = ('Item 1. Business Refer to the section titled “Item 1A-Risk Factors”, and '
            'other factors. Item 1A. Risk Factors The real one.')
    found = find_boundaries(normalise(text), "10-K")
    assert [b.section for b in found] == ["Item 1", "Item 1A"]
    assert found[1].start > text.index("The real one") - 40


def test_a_closing_quote_in_front_of_a_heading_does_not_hide_it():
    """Walmart: `...see "Item 1. Business."ITEM 3.LEGAL PROCEEDINGS`. The quote
    before ITEM 3 closes the sentence before it; it does not quote this one."""
    text = ('Item 2. Properties We rent. For more see "Item 1. Business."'
            'ITEM 3.LEGAL PROCEEDINGS We are in court.')
    assert "Item 3" in sections(text)


# ------------------------------------------------------------- the run-on headings
def test_a_title_running_straight_on_with_no_space():
    """Walmart's contents page reads `Item 1CCybersecurity28Item 2 Properties30`.
    There is no word boundary between C and C."""
    text = "Item 1CCybersecurity Our programme is run by the CISO."
    assert sections(text) == ["Item 1C"]


def test_the_title_decides_an_ambiguous_suffix():
    """`Item 1Business` and `Item 1BUnresolved Staff Comments` are the same
    shape. Only the title says which number it is."""
    assert sections("Item 1Business We make things.") == ["Item 1"]
    assert sections("Item 1BUnresolved Staff Comments None.") == ["Item 1B"]


def test_the_word_item_run_into_the_text_before_it():
    """`PART IIIItem10.Directors` and `Table of ContentsITEM 7. MANAGEMENT'S`.
    A word boundary in front of Item rejects most real headings in the corpus."""
    assert sections("PART IIIItem10.Directors, Executive Officers and Corporate "
                    "Governance Incorporated by reference.") == ["Item 10"]
    assert sections("Table of ContentsITEM 7. MANAGEMENT’S DISCUSSION AND ANALYSIS "
                    "Revenue rose.") == ["Item 7"]


def test_reserved_is_written_three_different_ways():
    for body in ("Item 6. [Reserved]", "Item 6. (Reserved)", "Item 6. Reserved"):
        assert sections(body + " Item 7. Management’s Discussion Revenue rose.") \
            == ["Item 6", "Item 7"], body


# ------------------------------------------------------------------- the ordering
def test_one_bad_early_match_does_not_delete_the_sections_behind_it():
    """A survivor from a cross-reference early in the document used to drop
    every real heading numbered below it. The longest forward run costs that
    one match instead."""
    text = ("Item 9A. Controls and Procedures mentioned early out of order. "
            "Item 1. Business A. Item 1A. Risk Factors B. Item 2. Properties C. "
            "Item 3. Legal Proceedings D.")
    assert sections(text) == ["Item 1", "Item 1A", "Item 2", "Item 3"]


def test_the_rule_travels_with_the_boundary():
    """Callers are told how a boundary was decided, because detection is a
    heuristic on documents it has not seen."""
    for b in find_boundaries(normalise("Item 1. Business A. Item 2. Properties B."), "10-K"):
        assert b.rule == "ordered"


# -------------------------------------------------------------------- normalise
def test_normalise_collapses_newlines_too():
    """Microsoft's contents page puts fourteen newlines between entries.
    Measuring the gap over that measures the markup."""
    assert normalise("a\n \n \n  b") == "a b"


def test_offsets_index_the_normalised_text():
    text = "Item 1.\n\n Business  We make things."
    span = split(text, "10-K")[0]
    assert normalise(text)[span.start:span.end] == span.text


def test_a_document_with_no_headings_gives_no_spans():
    assert split("A press release with no Items in it at all.", "10-K") == []


# ------------------------------------------------- one vocabulary per form
#: A quarterly report shaped like a real one: Part I carries the statements and
#: the MD&A and is long, Part II is mostly "None." That shape matters -- a run
#: of headings with almost nothing between them is how a contents page is told
#: from a document, and Part II on its own is short enough not to trip it.
BODY = "The accompanying notes are an integral part of these statements. " * 6

TEN_Q = f"""
PART I FINANCIAL INFORMATION
Item 1. Financial Statements  Condensed consolidated balance sheets follow. {BODY}
Item 2. Management's Discussion and Analysis of Financial Condition  Revenue rose 8%. {BODY}
Item 3. Quantitative and Qualitative Disclosures About Market Risk  Rate exposure. {BODY}
Item 4. Controls and Procedures  Disclosure controls were effective. {BODY}
PART II OTHER INFORMATION
Item 1. Legal Proceedings  None material.
Item 1A. Risk Factors  See the Annual Report on Form 10-K.
Item 2. Unregistered Sales of Equity Securities and Use of Proceeds  None.
Item 3. Defaults Upon Senior Securities  None.
Item 5. Other Information  Nothing to report.
Item 6. Exhibits  31.1, 31.2, 32.1
"""


def test_a_ten_q_splits_into_its_items_with_the_part_in_the_name():
    """1 to 3 sections of about ten before this, missing the statements and
    the MD&A -- which is exactly what a number cannot be traced through."""
    from qanat.sections import split

    spans = split(TEN_Q, "10-Q")
    found = [s.section for s in spans]
    assert found == [
        "Part I Item 1", "Part I Item 2", "Part I Item 3", "Part I Item 4",
        "Part II Item 1", "Part II Item 1A", "Part II Item 2", "Part II Item 3",
        "Part II Item 5", "Part II Item 6",
    ]
    # The two that were missing are the two that matter.
    assert "Part I Item 1" in found, "the financial statements"
    assert "Part I Item 2" in found, "the MD&A"
    for s in spans:
        assert s.rule in ("title", "ordered"), "the rule travels with every span"
        assert s.end > s.start


def test_the_same_item_number_in_two_parts_is_two_sections():
    """Resolving the Part away would merge the statements with the lawsuits."""
    from qanat.sections import split

    by_name = {s.section: s for s in split(TEN_Q, "10-Q")}
    assert "balance sheets" in by_name["Part I Item 1"].text.lower()
    assert "none material" in by_name["Part II Item 1"].text.lower()
    assert by_name["Part I Item 1"].start < by_name["Part II Item 1"].start


def test_the_part_restart_does_not_read_as_the_document_going_backwards():
    """Part II Item 1 comes after Part I Item 4. Ranked on the number alone
    that is a step back, and the ordered run would drop half the document."""
    from qanat.sections import _rank

    assert _rank("Part I Item 4") < _rank("Part II Item 1")
    assert _rank("Part II Item 1") < _rank("Part II Item 1A")
    assert _rank("Part II Item 1A") < _rank("Part II Item 2")
    # A form without Parts is unaffected.
    assert _rank("Item 1") < _rank("Item 1A") < _rank("Item 2") < _rank("Item 10")


def test_a_decimal_item_number_never_matches():
    """`Item 7.01` is the 8-K's scheme. It is not Item 7 of anything here."""
    from qanat.sections import find_boundaries, normalise, split

    eight_k = normalise(
        "Item 7.01 Regulation FD Disclosure. On 30 October the company issued a press "
        "release. Item 1.01 Entry into a Material Definitive Agreement. "
        "Item 8.01 Other Events. Item 9.01 Financial Statements and Exhibits."
    )
    # Microsoft's 8-K came back as Item 1, Item 7 and Item 8. Not any more --
    # and not even when the 10-K's own vocabulary is forced on it.
    assert find_boundaries(eight_k, "10-K") == []
    assert split(eight_k, "8-K") == []

    # The guard is in the regex, so it holds whatever vocabulary is handed in.
    from qanat.sections import FORM_10K, _hits, _one_part

    assert _hits(eight_k, _one_part(FORM_10K)) == []


def test_a_real_item_seven_still_matches_with_a_full_stop_after_it():
    """The guard rejects `7.01` and must not reject `7. Management's`."""
    from qanat.sections import find_boundaries, normalise

    text = normalise("Item 7. Management's Discussion and Analysis  Revenue rose.")
    assert [b.section for b in find_boundaries(text, "10-K")] == ["Item 7"]


def test_a_def_fourteen_a_produces_no_item_seven():
    """Oracle's yielded one, from a heading that is not an Item."""
    from qanat.sections import normalise, split

    proxy = normalise(
        "Notice of Annual Meeting of Stockholders. Item 7. The Board recommends a vote "
        "FOR each nominee named in this proxy statement."
    )
    assert split(proxy, "DEF 14A") == []


def test_a_form_with_no_vocabulary_returns_an_empty_span_list():
    """Zero is the honest answer. A small number is not."""
    from qanat.sections import split, vocabulary

    for form in ("20-F", "6-K", "8-K", "DEF 14A", "11-K", "13F-HR", "ZZ-99"):
        assert vocabulary(form) == {}, f"{form} should have no vocabulary"
        assert split(TEN_Q, form) == [], f"{form} found sections it does not have"


def test_no_vocabulary_ships_for_a_pile_that_is_not_built():
    """A vocabulary for a form nothing reads is a claim with nothing behind it."""
    from qanat.sections import VOCABULARIES

    assert set(VOCABULARIES) == {"10-K", "10-Q"}


def test_split_cannot_be_called_without_a_form():
    """It used to default to the 10-K's vocabulary, which is how every other
    form came back cut at a 10-K's headings."""
    import inspect

    from qanat.sections import find_boundaries, split

    for fn in (split, find_boundaries):
        params = inspect.signature(fn).parameters
        assert "form" in params, f"{fn.__name__} does not take a form"
        assert params["form"].default is inspect.Parameter.empty, \
            f"{fn.__name__}'s form has a default, so a caller can fall through to one"

    with pytest.raises(TypeError):
        split(TEN_Q)


def test_no_call_anywhere_passes_text_alone_and_gets_ten_k_behaviour():
    """The grep the card asks for, as a test that keeps holding."""
    import re as _re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # `split(` or `find_boundaries(` with exactly one argument: no comma before
    # the closing bracket. Matched across one line, which is how every call in
    # this repo is written.
    one_arg = _re.compile(r"\b(?:sections\.)?(?:split|find_boundaries)\("
                          r"[^(),]*(?:\([^()]*\))?[^(),]*\)")
    offenders = []
    for path in [*root.joinpath("src").rglob("*.py"),
                 *root.joinpath("examples").rglob("*.py"),
                 *root.joinpath("tests").rglob("*.py")]:
        if path.name == "test_sections.py":
            continue            # this file names the pattern in order to test it
        for i, line in enumerate(path.read_text().splitlines(), 1):
            if ".str.split(" in line or "str.split(" in line:
                continue
            for m in one_arg.finditer(line):
                # A `.split(` on a string is a different function entirely.
                if m.group(0).lstrip().startswith("."):
                    continue
                before = line[:m.start()]
                if before.rstrip().endswith("."):
                    continue
                offenders.append(f"{path.relative_to(root)}:{i}: {line.strip()}")
    assert offenders == [], "a call that gets a vocabulary it did not ask for:\n" + \
        "\n".join(offenders)


def test_the_ten_k_path_is_unchanged():
    """Same document, same sections, same rules as before the form was added."""
    from qanat.sections import find_boundaries, normalise

    text = normalise(
        "Item 1. Business We make things. "
        "Item 1A. Risk Factors Many. "
        "Item 2. Properties Several. "
        "Item 7. Management's Discussion Revenue rose. "
        "Item 8. Financial Statements and Supplementary Data Here."
    )
    found = find_boundaries(text, "10-K")
    assert [b.section for b in found] == [
        "Item 1", "Item 1A", "Item 2", "Item 7", "Item 8"]
    assert {b.rule for b in found} == {"ordered"}


def test_a_ten_qs_contents_page_is_dropped_like_a_ten_ks():
    """11 Items listed in a row with a page number between them is a listing.

    Worth its own test because a 10-Q has half as many Items as a 10-K, so the
    run that identifies a contents page is half as long -- and a terse Part II
    is the shape that comes closest to looking like one.
    """
    from qanat.sections import split

    contents = (
        "PART I FINANCIAL INFORMATION "
        "Item 1. Financial Statements 3 "
        "Item 2. Management's Discussion and Analysis of Financial Condition 24 "
        "Item 3. Quantitative and Qualitative Disclosures About Market Risk 31 "
        "Item 4. Controls and Procedures 31 "
        "PART II OTHER INFORMATION "
        "Item 1. Legal Proceedings 32 "
        "Item 1A. Risk Factors 32 "
        "Item 2. Unregistered Sales of Equity Securities and Use of Proceeds 33 "
        "Item 3. Defaults Upon Senior Securities 33 "
        "Item 4. Mine Safety Disclosures 33 "
        "Item 5. Other Information 33 "
        "Item 6. Exhibits 34 "
    )
    assert split(contents, "10-Q") == [], "the contents page is not the document"

    # And the same listing in front of the real thing does not become it.
    spans = split(contents + TEN_Q, "10-Q")
    assert [s.section for s in spans][:2] == ["Part I Item 1", "Part I Item 2"]
    assert spans[0].start > len(contents) - 100, "the body, not the listing"


def test_a_ten_q_cross_reference_to_the_ten_k_is_not_a_heading():
    """Part II Item 1A is usually one sentence pointing at the annual report."""
    from qanat.sections import find_boundaries, normalise

    text = normalise(
        "Item 1. Financial Statements  Balance sheets. "
        "There have been no material changes from the risk factors described in "
        'Item 1A, "Risk Factors," of our Annual Report on Form 10-K.'
    )
    found = [b.section for b in find_boundaries(text, "10-Q")]
    assert found == ["Part I Item 1"], f"got {found}"
