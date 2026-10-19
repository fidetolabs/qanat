"""Section detection, on the shapes that actually broke it.

Every case here is a real one taken from the forty filings in
`examples/sec-10k`, reduced to the few hundred characters that mattered. They
are kept because each one was found by a document rather than imagined, and
each would otherwise come back.
"""

from __future__ import annotations

from qanat.sections import FORM_10K, find_boundaries, normalise, split


def sections(text: str) -> list[str]:
    return [b.section for b in find_boundaries(normalise(text))]


def test_a_plain_run_of_headings():
    text = ("Item 1. Business We make things. "
            "Item 1A. Risk Factors Many things could go wrong. "
            "Item 2. Properties We rent offices.")
    assert sections(text) == ["Item 1", "Item 1A", "Item 2"]


def test_spans_run_to_the_next_heading():
    text = "Item 1. Business We make things. Item 1A. Risk Factors It could go wrong."
    spans = split(text)
    assert spans[0].text.strip() == "Item 1. Business We make things."
    assert spans[1].start == spans[0].end
    assert spans[-1].end == len(normalise(text))


def test_every_span_starts_with_its_own_heading():
    text = "Item 1. Business A. Item 1A. Risk Factors B. Item 2. Properties C."
    for s in split(text):
        assert s.text.lower().replace(" ", "").startswith(s.section.lower().replace(" ", ""))


# --------------------------------------------------------------- the contents page
def test_the_contents_page_is_not_the_document():
    """Every Item name packed into one run, each with a page number after it.
    A naive search finds this first and calls the whole filing Item 1."""
    toc = "".join(f"Item {k}.Title{i + 3}" for i, k in enumerate(FORM_10K))
    text = toc + " Item 1. Business The real one starts here."
    found = find_boundaries(normalise(text))
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
    found = find_boundaries(normalise(text))
    assert [b.section for b in found] == ["Item 1", "Item 1A"]
    assert found[1].start > text.index("The real section") - 40


def test_a_quoted_reference_carrying_the_real_title_is_still_a_reference():
    """Costco: `the factors set forth in the section titled "Item 1A-Risk
    Factors"`. The title is right there, so only the quoting gives it away."""
    text = ('Item 1. Business Refer to the section titled “Item 1A-Risk Factors”, and '
            'other factors. Item 1A. Risk Factors The real one.')
    found = find_boundaries(normalise(text))
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
    for b in find_boundaries(normalise("Item 1. Business A. Item 2. Properties B.")):
        assert b.rule == "ordered"


# -------------------------------------------------------------------- normalise
def test_normalise_collapses_newlines_too():
    """Microsoft's contents page puts fourteen newlines between entries.
    Measuring the gap over that measures the markup."""
    assert normalise("a\n \n \n  b") == "a b"


def test_offsets_index_the_normalised_text():
    text = "Item 1.\n\n Business  We make things."
    span = split(text)[0]
    assert normalise(text)[span.start:span.end] == span.text


def test_a_document_with_no_headings_gives_no_spans():
    assert split("A press release with no Items in it at all.") == []
