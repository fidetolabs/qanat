"""Every tagged number in every filing, as rows of `facts`.

The parse itself is `qanat.xbrl`, which knows about inline XBRL and nothing
about the SEC. This step is the join between a landed document and that reader:
it opens the gzip, parses it, and stamps each fact with the day the document
became public.

**`as_of` is the filing date, not the period end.** A fact about the year to
September 2025 was not knowable until the filing landed in October, and a replay
that reads it any earlier is reading the future. The `facts` table gets its own
clock for exactly this reason.
"""

import gzip
import json

import pandas as pd

from qanat.xbrl import parse, read_facts, taxonomy

#: What makes two facts the same fact. `fact_id` is deliberately not in it:
#: it identifies the tag, not the number.
IDENTITY = [
    "document", "concept", "dims", "period_start", "period_end", "is_instant",
    "value", "unit",
]

COLUMNS = [
    "document", "subject", "concept", "dims", "period_start", "period_end",
    "is_instant", "value", "unit", "decimals", "fact_id", "taxonomy",
    "source", "as_of",
]


def run(ctx):
    docs = ctx.read("raw.documents")
    rows = []
    for doc in docs.itertuples():
        path = ctx.root / doc.body_path
        if not path.exists():
            ctx.log(f"{doc.id}: {doc.body_path} is missing, skipped", "warn")
            continue
        raw = gzip.decompress(path.read_bytes())
        version = taxonomy(raw)
        facts = read_facts(raw)
        if not facts:
            ctx.log(f"{doc.id}: no inline XBRL found", "warn")
        for f in facts:
            rows.append({
                "document": doc.id,
                "subject": f.subject or doc.entity,
                "concept": f.concept,
                "dims": f.dims,
                "period_start": f.period_start,
                "period_end": f.period_end,
                "is_instant": f.is_instant,
                "value": f.value,
                "unit": f.unit,
                "decimals": f.decimals,
                "fact_id": f.fact_id,
                "taxonomy": version,
                "source": doc.source,
                # The day this became public. The whole point-in-time story
                # rests on this column and not on period_end.
                "as_of": doc.published_at,
            })
        ctx.log(f"{doc.id}: {len(facts)} facts, {version or 'taxonomy unknown'}")

    df = pd.DataFrame(rows, columns=COLUMNS)
    if df.empty:
        return df
    for col in ("period_start", "period_end", "as_of"):
        df[col] = pd.to_datetime(df[col], errors="coerce")

    # One number tagged in four places is one number. A filer tags the same
    # figure in the statement, the segment note and the MD&A, and each gets its
    # own `fact_id` -- so the rows differ by id and by nothing that matters.
    # Keeping all four would make `count(*)` a measure of how often a filer
    # repeated itself. The first id survives, so a fact still points at a place
    # in the document.
    before = len(df)
    df = df.drop_duplicates(IDENTITY, keep="first").reset_index(drop=True)
    if before != len(df):
        ctx.log(f"{before - len(df)} repeated tags collapsed, {len(df)} facts kept")
    return df
