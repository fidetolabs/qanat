"""Each filing split into its Items, as rows of `spans`.

One row is one section: which document, which Item, the character range it
occupies, and the text. Offsets index `text_of(raw)` below and nothing else: a
caller re-reading a span has to arrive at the same string, so the stripping and
the normalising both live in one function.

**`rule` says how the boundary was decided, and it is not decoration.** A 10-K
names every Item on its contents page and again in cross-references through the
prose, so most matches for "Item 1A" are not the heading. A tool that returns a
confident wrong offset is worse than one that admits it guessed, so the rule
travels with the row and a caller can distrust it.

`found` and `missing` go in the log rather than the table: a section that is
absent has no character range, and a row with no offsets is not a span.

**The form decides the vocabulary, and a form without one gets no spans.** A
10-Q numbers its Items inside Parts and a 10-K numbers them straight through, so
one vocabulary applied to both finds Item 1A and Item 4 of a quarterly report
and misses the statements and the MD&A. Anything else -- an 8-K, a DEF 14A -- is
numbered differently again, and would come back cut at a 10-K's headings. So the
row's own `doc_type` is passed in, and `split` returns nothing for a form it has
no vocabulary for.
"""

import gzip
import html as html_module
import re

import pandas as pd

from qanat.sections import normalise, split, vocabulary

#: Markup that carries no prose. Dropped whole, because a `<style>` body is
#: CSS that would otherwise land in the middle of a section's text.
NOT_PROSE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")
TAG = re.compile(r"<[^>]+>")

COLUMNS = [
    "document", "section", "char_start", "char_end", "n_chars", "rule",
    "text", "source", "as_of",
]


def text_of(raw):
    """The filing's prose, in the one string offsets are defined against.

    **A tag becomes a space rather than nothing.** This used to be
    `"".join(parse(raw).itertext())`, which closes one table cell against the
    next: `<td>12</td><td>34</td>` reads as 1,234, and a CIK beside a year came
    back as `00003201932025`. Section detection was unharmed -- the same 11
    spans either way on a 10-Q -- but `read_section` returns this text, so an
    agent read glued numbers out of a table.
    """
    text = TAG.sub(" ", NOT_PROSE.sub(" ", raw.decode("utf-8", "replace")))
    return normalise(html_module.unescape(text))


def expected(form):
    """Every section this form has, so the log can name what was missed."""
    return sorted({name for options in vocabulary(form).values()
                   for name, _pattern in options})


def run(ctx):
    docs = ctx.read("raw.documents")
    rows = []
    for doc in docs.itertuples():
        path = ctx.root / doc.body_path
        if not path.exists():
            ctx.log(f"{doc.id}: {doc.body_path} is missing, skipped", "warn")
            continue
        form = getattr(doc, "doc_type", None)
        wanted = expected(form)
        if not wanted:
            ctx.log(f"{doc.id}: no vocabulary for {form}, so no sections. The facts "
                    f"still parse", "info")
            continue
        text = text_of(gzip.decompress(path.read_bytes()))
        spans = split(text, form)
        for s in spans:
            rows.append({
                "document": doc.id,
                "section": s.section,
                "char_start": s.start,
                "char_end": s.end,
                "n_chars": s.end - s.start,
                "rule": s.rule,
                "text": s.text,
                "source": doc.source,
                "as_of": doc.published_at,
            })
        missing = [x for x in wanted if x not in {s.section for s in spans}]
        level = "warn" if len(missing) > 4 else "info"
        ctx.log(
            f"{doc.id}: {len(spans)}/{len(wanted)} {form} sections"
            + (f", missing {', '.join(missing)}" if missing else ""),
            level,
        )

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["as_of"] = pd.to_datetime(df["as_of"], errors="coerce")
    return df
