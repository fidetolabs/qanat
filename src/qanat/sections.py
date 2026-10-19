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
_ITEM = re.compile(
    r"Item(?:\s+|(?=\d{1,2}[A-C]?\s*[.:—–-]))\s*(\d{1,2})([A-C]?)(?![0-9])\s*[.:—–-]?\s*",
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
    """One `Item N` whose number the title has already confirmed."""

    num: str
    start: int
    end: int


def _hits(text: str, titles: dict[str, str]) -> list[_Hit]:
    """Every Item reference whose own title follows it.

    `Item 1Business` and `Item 1BUnresolved Staff Comments` are the same shape,
    so both readings of the suffix are tried and the title settles it. Where
    neither reading has its title after it, the match is not a heading and is
    not returned at all -- that is what removes the thousands of running page
    headers Microsoft puts on every page.
    """
    out: list[_Hit] = []
    for m in _ITEM.finditer(text):
        digits, letter = m.group(1), (m.group(2) or "").upper()
        # Longest reading first: `1C` before `1`.
        for num, end in ((digits + letter, m.end()), (digits, m.end() - len(letter))):
            if letter and num == digits:
                # Re-read the separator that the letter had swallowed.
                tail = re.match(r"\s*[.:—–-]?\s*", text[end:])
                end = end + tail.end() if tail else end
            pattern = titles.get(num)
            if pattern and re.match(pattern, text[end:end + 90], re.IGNORECASE):
                out.append(_Hit(num, m.start(), end))
                break
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
                and _rank(h.num) > _rank(current[-1].num):
            current.append(h)
        else:
            runs.append(current)
            current = [h]
    runs.append(current)
    return [(r[0].start, r[-1].end) for r in runs if len(r) >= _TOC_RUN]


def find_boundaries(text: str, titles: dict[str, str] | None = None) -> list[Boundary]:
    """Where each section starts, and which rule decided it.

    `rule` is one of:

        title        the Item number was followed by that Item's own title
        ordered      a title match that also arrived in document order

    Candidates rejected as a contents entry or a cross-reference are dropped
    rather than returned, because a span with no body is not a span. What a
    caller can tell from the result is which sections were found at all, and
    `list_sections` reports the misses.
    """
    titles = titles or FORM_10K
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
        found.append(Boundary(section=f"Item {h.num}", start=h.start, rule="title"))

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


def _rank(section: str) -> tuple[int, str]:
    num = section.split()[-1]
    digits = re.match(r"(\d+)([A-C]?)", num)
    return (int(digits.group(1)), digits.group(2)) if digits else (99, "")


def split(text: str, titles: dict[str, str] | None = None) -> list[Span]:
    """Every section found, each running to the start of the next."""
    text = normalise(text)
    bounds = find_boundaries(text, titles)
    spans: list[Span] = []
    for i, b in enumerate(bounds):
        end = bounds[i + 1].start if i + 1 < len(bounds) else len(text)
        spans.append(Span(b.section, b.start, end, b.rule, text[b.start:end]))
    return spans


__all__ = ["FORM_10K", "Boundary", "Span", "find_boundaries", "normalise", "split"]
