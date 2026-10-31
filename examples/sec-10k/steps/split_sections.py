"""Each filing split into its Items, as rows of `spans`.

One row is one section: which document, which Item, the character range it
occupies, and the text. Offsets are into the normalised text -- runs of
whitespace collapsed -- which is what `qanat.sections.normalise` returns and the
only string the offsets mean anything against.

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

import pandas as pd

from qanat.sections import normalise, split, vocabulary
from qanat.xbrl import parse

COLUMNS = [
    "document", "section", "char_start", "char_end", "n_chars", "rule",
    "text", "source", "as_of",
]


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
        text = normalise("".join(parse(gzip.decompress(path.read_bytes())).itertext()))
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
