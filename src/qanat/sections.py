"""Split a filing into named sections, and say how each boundary was decided.

Finding "Item 1A. Risk Factors" in a 10-K is harder than it reads, and getting
it wrong is expensive, because every later tool trusts the offsets.

Three things in a document match the obvious pattern and only one of them is a
heading.

**The contents page.** A 10-K lists every Item at the front. In Apple's 2025
filing all twenty-three Item strings sit within 1,200 characters of each other
at offset 18,267. A naive search finds that first and returns a whole document
as "Item 1".

**Cross-references.** The prose is full of *"described in Item 1A of this Form
10-K"* and *"see Item 7, 'Management's Discussion'"*. Apple's filing has
nineteen of these, more than the fourteen real headings.

**The heading itself**, which is the Item number followed by that Item's actual
title.

So the title is the signal, and the rule each boundary matched is stored next to
the span. **A boundary tool that returns a confident wrong offset is worse than
one that says it was unsure**, and a caller that can read `rule` can decide how
much to trust an answer. Until detection is good, that is the honest interface.

`FORM_10K` is the SEC's vocabulary, not the platform's. It lives here as data a
source adapter supplies, which is what keeps `spans` itself general: a document,
a section name, two offsets.

## One vocabulary per form, and no guessing when there is none

**A form whose numbering merely resembles the 10-K's returns a confident wrong
offset**, which is the failure this module exists to avoid. Measured: an 8-K
split with the 10-K's vocabulary produced Item 1, Item 7 and Item 8 -- those are
`1.01`, `7.01` and `8.01`, the 8-K's own scheme, read through a pattern for a
different form. A DEF 14A produced an Item 7 from a heading that is not one.

So `split` is told which form it is working in, and **a form with no vocabulary
returns no spans.** Zero is the honest answer and a small number is not: the
reading tools report no sections, which degrades cleanly, where three wrong ones
look like an answer.

**A decimal Item number is declined outright.** `Item 7.01` is not Item 7 in any
vocabulary here; it is a different numbering scheme, and the regex rejects it
rather than relying on a title test to fail.

## The Part is part of the section name

A 10-K numbers its Items once, straight through. **A 10-Q numbers them twice** --
Part I Items 1 to 4 and Part II Items 1 to 6 -- so the same number is two
different sections depending on the Part, and resolving that away would merge
the financial statements with the legal proceedings. The Part therefore goes in
the name.

What makes that cheap is that the titles do not collide: Part I Item 1 is the
financial statements and Part II Item 1 is legal proceedings. **The title test
that already decides the number also decides the Part**, so no separate pass
over the Part headings is needed.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: Item number -> a pattern matching the start of that Item's real title. Filers
#: word these slightly differently, so each is the distinctive opening rather
#: than the full official heading.
FORM_10K: dict[str, str] = {
    "1": r"business",
    "1A": r"risk\s+factors",
    "1B": r"unresolved\s+staff\s+comments",
    "1C": r"cyber\s*security",
    "2": r"propert(?:y|ies)",
    "3": r"legal\s+proceedings",
    "4": r"mine\s+safety",
    "5": r"market\s+for\s+(?:the\s+)?registrant",
    # Filers write this one as [Reserved], (Reserved) or bare, and the older
    # ones still carry the heading it replaced.
    "6": r"(?:[\[(]?\s*reserved|selected\s+financial\s+data)",
    "7": r"management.{0,3}s\s+discussion",
    "7A": r"quantitative\s+and\s+qualitative",
    "8": r"financial\s+statements\s+and\s+supplementary",
    "9": r"changes\s+in\s+and\s+disagreements",
    "9A": r"controls\s+and\s+procedures",
    "9B": r"other\s+information",
    "9C": r"disclosure\s+regarding\s+foreign",
    "10": r"directors,?\s+executive\s+officers",
    "11": r"executive\s+compensation",
    "12": r"security\s+ownership",
    "13": r"certain\s+relationships",
    "14": r"principal\s+account(?:ant|ing)",
    "15": r"exhibits?\s*(?:,|and)?\s*(?:and\s+)?financial\s+statement",
    "16": r"form\s+10-k\s+summary",
}

#: The 10-Q's Items, by Part. A quarterly report numbers from 1 inside each
#: Part, so `Item 1` means the financial statements in Part I and legal
#: proceedings in Part II. Item 1A appears only in Part II.
FORM_10Q_PARTS: dict[str, dict[str, str]] = {
    "I": {
        "1": r"financial\s+statements",
        "2": r"management.{0,3}s\s+discussion",
        "3": r"quantitative\s+and\s+qualitative",
        "4": r"controls\s+and\s+procedures",
    },
    "II": {
        "1": r"legal\s+proceedings",
        "1A": r"risk\s+factors",
        "2": r"unregistered\s+sales",
        "3": r"defaults\s+upon\s+senior",
        "4": r"mine\s+safety",
        "5": r"other\s+information",
        "6": r"exhibits?",
    },
}

#: An Item number -> every section it could be, each a (name, title pattern).
#: More than one when a form numbers inside Parts.
Vocabulary = dict[str, list[tuple[str, str]]]


def _one_part(titles: dict[str, str]) -> Vocabulary:
    """A form that numbers its Items once, straight through."""
    return {num: [(f"Item {num}", pattern)] for num, pattern in titles.items()}


def _by_part(parts: dict[str, dict[str, str]]) -> Vocabulary:
    """A form that numbers its Items inside Parts, so the Part names the section."""
    out: Vocabulary = {}
    for part, titles in parts.items():
        for num, pattern in titles.items():
            out.setdefault(num, []).append((f"Part {part} Item {num}", pattern))
    return out


#: form -> its Item vocabulary. **A form absent from this table has none**, and
#: gets no spans rather than another form's offsets.
#:
#: Only the two forms whose facts are parsed are here. A 20-F numbers Items 1 to
#: 19 and a 6-K has no Items at all; neither is ingested, and shipping a
#: vocabulary for a form nothing reads would be a claim with nothing behind it.
VOCABULARIES: dict[str, Vocabulary] = {
    "10-K": _one_part(FORM_10K),
    "10-Q": _by_part(FORM_10Q_PARTS),
}


def vocabulary(form: str | None) -> Vocabulary:
    """The Item vocabulary for a form, or an empty one for a form without.

    An empty vocabulary finds nothing, which is the point: the caller gets no
    spans instead of a confident wrong offset.
    """
    if not form:
        return {}
    return VOCABULARIES.get(str(form).strip().upper(), {})


#: What follows an Item number when it is being referred to rather than opened.
_REFERENCE = re.compile(
    r"\s*(?:of\s+th|in\s+th|to\s+th|under|above|below|hereof|herein|and\s+Item|through\s+Item"
    r"|,\s*[“‘\"']|\s*[“‘\"'])",
    re.IGNORECASE,
)
#: The number, and a letter suffix that may or may not belong to it. Three of
#: the obvious ways to write this are wrong, and each cost a batch of headings.
#:
#: **No `\b` after the number.** Filers run the title straight on -- Walmart's
#: contents page reads `Item 1CCybersecurity28Item 2 Properties30` -- and there
#: is no word boundary between `C` and `C`, so the anchored version matched
#: none of them and silently lost the whole contents page. Dropping it creates
#: the opposite problem: `Item 1Business` parses as Item 1B just as happily as
#: Item 1. The number alone cannot settle that, so `_hits` tries both readings
#: and keeps the one whose title fits.
#:
#: **No `\b` in front of `Item` either.** Extracted text runs the page
#: furniture into the heading -- `Table of ContentsITEM 7. MANAGEMENT'S
#: DISCUSSION` -- and a boundary there rejects most real headings in the corpus.
#:
#: **The space after `Item` is optional, but only before a separator.** Meta's
#: Part III heading extracts as `PART IIIItem10.Directors, Executive Officers`,
#: and the `.` is what tells that from the many places a bare `Item` happens to
#: sit in front of a digit.
#: **And a decimal number is declined outright.** `Item 7.01` is the 8-K's
#: scheme, not Item 7 of anything here, and leaving it to the title test to
#: reject was how an 8-K came back holding Item 1, Item 7 and Item 8.
_ITEM = re.compile(
    r"Item(?:\s+|(?=\d{1,2}[A-C]?\s*[.:—–-]))\s*(\d{1,2})([A-C]?)(?![0-9])"
    r"(?!\s*\.\s*\d)\s*[.:—–-]?\s*",
    re.IGNORECASE,
)

#: A quote before the number can mean the document is naming the section rather
#: than starting it: `the section titled “Item 1A-Risk Factors”`. That one
#: carries the Item's real title inside the quotes, so the title test passes it
#: and only the quoting gives it away.
#:
#: A quote before it can equally be the *close* of the sentence before, which is
#: how Walmart's real Item 3 heading reads: `...see "Item 1. Business."ITEM
#: 3.LEGAL PROCEEDINGS`. A straight `"` is the same character either way, so
#: position alone cannot separate the two and the test has to be whether the
#: quote closes again shortly after the title.
_QUOTES = {'"': '"', "'": "'", "“": "”", "‘": "’", "`": "`"}

#: How far after the number to look for the closing quote. The longest Item
#: title -- Item 12's "Security Ownership of Certain Beneficial Owners and
#: Management and Related Stockholder Matters" -- is 93 characters, so a window
#: shorter than that reads the longest citations as headings.
_QUOTE_CLOSES_WITHIN = 140

#: Punctuation before a quote mark means the quote is closing, not opening. For
#: a straight `"` that is the only way to tell, and it is what separates
#: Walmart's real heading -- `see "Item 1. Business."ITEM 3.LEGAL PROCEEDINGS`
#: -- from a citation of one.
_SENTENCE_END = set(".,;:!?")

#: A contents page is a *run* of Item matches with almost nothing between them:
#: a title, a page number, then the next Item. Real headings have a section of
#: prose in between. Density over a fixed window is the wrong test, because
#: Part III of a 10-K is a dozen genuine headings whose bodies are one sentence
#: each ("The information required by this Item is incorporated by reference"),
#: and it looks dense too.
_TOC_GAP = 200      # characters between neighbours inside a listing
_TOC_RUN = 10       # how many in a row before it is a listing and not a document


class Boundary(NamedTuple):
    section: str
    start: int
    rule: str


class Span(NamedTuple):
    section: str
    start: int
    end: int
    rule: str
    text: str


def normalise(text: str) -> str:
    """Collapse every run of whitespace to a single space.

    This changes the length of the string, so it is the one place offsets are
    defined. Offsets stored in `spans` index the string this returns and
    nothing else, which is why it is exported rather than inlined: a caller
    re-reading a span has to normalise the same way.

    Newlines collapse along with spaces, and that is load-bearing rather than
    tidiness. Text pulled out of HTML carries runs of twenty newlines wherever
    the markup had nested empty elements, and Microsoft's contents page puts
    fourteen of them between one entry and the next. Measuring the distance
    between two headings across that measures the markup rather than the
    document, and the contents page then reads as though it had prose in it.
    """
    return re.sub(r"\s+", " ", text)


class _Hit(NamedTuple):
    """One `Item N` whose number the title has already confirmed.

    `section` is the full name, which carries the Part for a form that numbers
    inside Parts -- the title is what decided which Part, so this is where that
    decision is recorded.
    """

    section: str
    start: int
    end: int


def _hits(text: str, titles: Vocabulary) -> list[_Hit]:
    """Every Item reference whose own title follows it.

    `Item 1Business` and `Item 1BUnresolved Staff Comments` are the same shape,
    so both readings of the suffix are tried and the title settles it. Where
    neither reading has its title after it, the match is not a heading and is
    not returned at all -- that is what removes the thousands of running page
    headers Microsoft puts on every page.

    **The title also settles the Part**, where there is one. A 10-Q's Item 1 is
    the financial statements in Part I and legal proceedings in Part II, and
    those two patterns do not both match the same text.
    """
    out: list[_Hit] = []
    for m in _ITEM.finditer(text):
        digits, letter = m.group(1), (m.group(2) or "").upper()
        hit = None
        # Longest reading first: `1C` before `1`.
        for num, end in ((digits + letter, m.end()), (digits, m.end() - len(letter))):
            if letter and num == digits:
                # Re-read the separator that the letter had swallowed.
                tail = re.match(r"\s*[.:—–-]?\s*", text[end:])
                end = end + tail.end() if tail else end
            for name, pattern in titles.get(num, ()):
                if re.match(pattern, text[end:end + 90], re.IGNORECASE):
                    hit = _Hit(name, m.start(), end)
                    break
            if hit is not None:
                break
        if hit is not None:
            out.append(hit)
    return out


def _is_quoted(text: str, hit: _Hit) -> bool:
    """Is this heading sitting inside quotation marks, being named rather than opened?

    Two things have to hold. The quote in front has to be an *opening* one --
    for a straight `"` the only evidence is that no sentence punctuation sits
    behind it -- and it has to close again within a title's length. A quote
    that opens and never closes nearby belongs to a sentence that happens to
    end where this heading begins.
    """
    if not hit.start:
        return False
    close = _QUOTES.get(text[hit.start - 1])
    if not close:
        return False
    if hit.start > 1 and text[hit.start - 2] in _SENTENCE_END:
        return False
    return close in text[hit.end:hit.end + _QUOTE_CLOSES_WITHIN]


def _toc_ranges(hits: list[_Hit]) -> list[tuple[int, int]]:
    """Character ranges that are a contents listing rather than document body.

    A run of Item headings, each within `_TOC_GAP` of the last and numbered in
    increasing order, is a listing once it reaches `_TOC_RUN` entries. The
    ordering test is what separates a contents page from a cluster of
    cross-references, which repeat and jump around.
    """
    runs: list[list[_Hit]] = []
    current: list[_Hit] = []
    for h in hits:
        if current and h.start - current[-1].end <= _TOC_GAP \
                and _rank(h.section) > _rank(current[-1].section):
            current.append(h)
        else:
            runs.append(current)
            current = [h]
    runs.append(current)
    return [(r[0].start, r[-1].end) for r in runs if len(r) >= _TOC_RUN]


def find_boundaries(text: str, form: str | None,
                    *, titles: Vocabulary | None = None) -> list[Boundary]:
    """Where each section starts, and which rule decided it.

    `rule` is one of:

        title        the Item number was followed by that Item's own title
        ordered      a title match that also arrived in document order

    Candidates rejected as a contents entry or a cross-reference are dropped
    rather than returned, because a span with no body is not a span. What a
    caller can tell from the result is which sections were found at all, and
    `list_sections` reports the misses.

    **`form` has no default.** It used to fall through to the 10-K's vocabulary,
    which is how an 8-K came back with three sections it does not have. A form
    with no vocabulary returns an empty list; pass `titles` to supply one
    directly.
    """
    titles = vocabulary(form) if titles is None else titles
    if not titles:
        return []
    hits = _hits(text, titles)
    toc = _toc_ranges(hits)

    found: list[Boundary] = []
    for h in hits:
        if any(lo <= h.start < hi for lo, hi in toc):
            continue
        if _REFERENCE.match(text, h.end):
            continue
        if _is_quoted(text, h):
            continue
        found.append(Boundary(section=h.section, start=h.start, rule="title"))

    # Keep one boundary per section -- the first body occurrence -- then take the
    # longest run that goes forwards through the document.
    first: dict[str, Boundary] = {}
    for b in found:
        first.setdefault(b.section, b)
    return _longest_ordered(sorted(first.values(), key=lambda b: b.start))


def _longest_ordered(bounds: list[Boundary]) -> list[Boundary]:
    """The longest subsequence whose sections increase in document order.

    A 10-K runs Item 1, 1A, 1B, 2, 3 ... 16 and never goes back. Dropping
    whatever breaks that is better than trusting position alone, because one
    survivor from a quoted cross-reference early in the document would
    otherwise delete every real heading numbered below it. Taking the longest
    run costs that one match instead of the eight behind it.
    """
    if not bounds:
        return []
    best = [1] * len(bounds)
    prev = [-1] * len(bounds)
    for i in range(len(bounds)):
        for j in range(i):
            if _rank(bounds[j].section) < _rank(bounds[i].section) and best[j] + 1 > best[i]:
                best[i], prev[i] = best[j] + 1, j
    i = best.index(max(best))
    out: list[Boundary] = []
    while i != -1:
        b = bounds[i]
        out.append(Boundary(b.section, b.start, "ordered"))
        i = prev[i]
    return out[::-1]


#: Part numbers, so `Part II Item 1` sorts after every Part I Item. A form
#: without Parts sorts as though every Item were in part 0.
_PART = {"I": 1, "II": 2, "III": 3, "IV": 4}


def _rank(section: str) -> tuple[int, int, str]:
    """Where this section sits in the form's order.

    Three places, because a 10-Q runs Part I Items 1 to 4 and then starts again
    at Part II Item 1. Ranking on the number alone would read that restart as
    the document going backwards, and `_longest_ordered` would drop half of it.
    """
    part = re.match(r"Part\s+([IVX]+)\s+Item", section, re.IGNORECASE)
    where = _PART.get(part.group(1).upper(), 9) if part else 0
    digits = re.search(r"Item\s+(\d+)([A-C]?)", section, re.IGNORECASE)
    if not digits:
        return (where, 99, "")
    return (where, int(digits.group(1)), (digits.group(2) or "").upper())


def split(text: str, form: str | None, *, titles: Vocabulary | None = None) -> list[Span]:
    """Every section found, each running to the start of the next.

    **`form` is required and may be None.** Passing it explicitly is the whole
    fix: the old signature defaulted to the 10-K's vocabulary, so handing it any
    other form produced that form's text cut at a 10-K's headings. None means
    no vocabulary and therefore no spans.
    """
    text = normalise(text)
    bounds = find_boundaries(text, form, titles=titles)
    spans: list[Span] = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1].start if i + 1 < len(bounds) else len(text)
        spans.append(Span(b.section, b.start, end, b.rule, text[b.start:end]))
    return spans


__all__ = ["FORM_10K", "FORM_10Q_PARTS", "VOCABULARIES", "Boundary", "Span",
           "Vocabulary", "find_boundaries", "normalise", "split", "vocabulary"]
