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
"""

import gzip

import pandas as pd

from qanat.sections import FORM_10K, normalise, split
from qanat.xbrl import parse

COLUMNS = [
    "document", "section", "char_start", "char_end", "n_chars", "rule",
    "text", "source", "as_of",
]
EXPECTED = [f"Item {k}" for k in FORM_10K]


def run(ctx):
    docs = ctx.read("raw.documents")
    rows = []
    for doc in docs.itertuples():
        path = ctx.root / doc.body_path
        if not path.exists():
            ctx.log(f"{doc.id}: {doc.body_path} is missing, skipped", "warn")
            continue
        text = normalise("".join(parse(gzip.decompress(path.read_bytes())).itertext()))
        spans = split(text)
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
        missing = [x for x in EXPECTED if x not in {s.section for s in spans}]
        level = "warn" if len(missing) > 4 else "info"
        ctx.log(
            f"{doc.id}: {len(spans)}/{len(EXPECTED)} sections"
            + (f", missing {', '.join(missing)}" if missing else ""),
            level,
        )

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    df["as_of"] = pd.to_datetime(df["as_of"], errors="coerce")
    return df
