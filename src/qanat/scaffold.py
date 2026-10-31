"""`qanat init` -- a project that runs green against real filings.

The starter pulls 10-K filings from SEC EDGAR, which needs no account and no
API key, only a contact address in the User-Agent. Two companies and two years
each, so a first run takes under a minute.

It stops at clean dated facts, and that is the whole shape. There is no metrics
stage and no results stage: a metric is a query over the facts and a screen is
a call that returns a list, so neither needs somewhere to land.
"""

from __future__ import annotations

from pathlib import Path

QANAT_YAML = """\
# What this pipeline is. `qanat check` enforces the rules; see docs/contract.md
project: {name}
# A directory, so the tables are parquet files read by DuckDB. A writer
# never blocks a reader, which is what lets two people share one project.
store: ./data

# ------------------------------------------------------------------ the stages --
# Order here is order in the pipeline. The first one holds what arrived and
# nobody edits it; the ones after hold what you worked out. The names are yours.
stages:
  - id: raw
    kind: raw
    description: landed exactly as it arrived, never edited
  - id: normalized
    kind: normalized
    description: typed and deduplicated -- the tagged numbers and the text

# ------------------------------------------------------------------- the clock --
# A filing has two dates: the period it describes, and the day it became
# public. This names the second one. Point it at the first and every screen
# reads numbers weeks before anybody could have known them.
time_columns:
  raw.documents: published_at
  normalized.facts: as_of

# ------------------------------------------------------------------ the sources --
# Many sources may feed one stage; each writes its own table.
sources:
  - id: filings
    to: [raw.documents]
    connector: sec          # swap for rest, sql or csv when you have a feed
    mode: replace
    key: [id]
    options:
      companies: [AAPL, MSFT]
      form: 10-K
      years: 2
      # The SEC refuses requests that carry no contact. Put yours here, or
      # export SEC_USER_AGENT and leave this line alone.
      user_agent: ${SEC_USER_AGENT}
      cache: ./data/documents

# ------------------------------------------------------------------- the steps --
# n:m -- `from` and `to` are both lists.
steps:
  - id: parse_facts
    from: [raw.documents]
    to:   [normalized.facts]
    script: steps/parse_facts.py
    options:
      reads: raw.documents

# ------------------------------------------------------------------ the corpus --
# Which tables hold the documents and the numbers pulled out of them. The
# reading tools need to know where to look, and the platform does not name
# layers -- so the project says. Without this they are simply not offered.
corpus:
  documents: raw.documents
  facts: normalized.facts

# ----------------------------------------------------------------- the metrics --
# A formula with a name and a version. Shared: `net income / revenue` is
# arithmetic, so it is the same number for every caller. The threshold you
# compare it against is the judgement, and that is yours -- it travels with the
# screen call and is never stored here.
#
# `origin: shipped` marks this as the **starter set**: one formula, so the
# product's own verb works on the day you install, and deliberately not a
# metric library. Call `define_metric` to write your own beside it; yours is
# labelled `defined` and does not join this set by being stored next to it.
#
# **The version is yours to bump.** An answer somebody kept points at a
# version, so a changed formula is written as a new version beside the old one
# and both still resolve.
metrics:
  - id: net_margin
    origin: shipped
    version: 1
    unit: ratio
    description: >
      net income over revenue, for the most recent full year each filer has
      reported as of the date being screened. Filers do not agree on a revenue
      tag -- NVIDIA uses Revenues, Apple uses the contract tag -- so both are
      accepted and the contract one wins when a filer tags both.
    sql: |
      WITH annual AS (
        SELECT subject, concept, value, as_of, period_end
        FROM normalized__facts
        WHERE dims = '{}' AND NOT is_instant
          AND date_diff('day', period_start, period_end) BETWEEN 350 AND 380
          AND as_of <= :as_of
      ), ni AS (
        SELECT subject, value, row_number() OVER (PARTITION BY subject
                 ORDER BY as_of DESC, period_end DESC) AS rn
        FROM annual WHERE concept = 'us-gaap:NetIncomeLoss'
      ), rev AS (
        SELECT subject, value, row_number() OVER (PARTITION BY subject
                 ORDER BY as_of DESC, period_end DESC,
                 CASE concept
                   WHEN 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' THEN 0
                   ELSE 1 END) AS rn
        FROM annual WHERE concept IN (
          'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
          'us-gaap:Revenues')
      )
      SELECT ni.subject AS subject, ni.value / NULLIF(rev.value, 0) AS value
      FROM ni JOIN rev ON rev.subject = ni.subject AND rev.rn = 1
      WHERE ni.rn = 1
"""

PARSE_FACTS_PY = '''\
"""Every tagged number in every filing, as rows of `facts`.

`qanat.xbrl` does the reading. It knows inline XBRL and nothing about the SEC,
so this step is just the join: open the gzip, parse it, and stamp each fact
with the day the document became public.
"""

import gzip

import pandas as pd

from qanat.xbrl import parse, read_facts, taxonomy

#: What makes two facts the same fact. A filer tags one figure in the
#: statement, the segment note and the MD&A, and each tag gets its own id --
#: so the rows differ by id and by nothing that matters.
IDENTITY = [
    "document", "concept", "dims", "period_start", "period_end", "is_instant",
    "value", "unit",
]


def run(ctx):
    rows = []
    for doc in ctx.read("raw.documents").itertuples():
        path = ctx.root / doc.body_path
        if not path.exists():
            ctx.log(f"{doc.id}: {doc.body_path} is missing, skipped", "warn")
            continue
        raw = gzip.decompress(path.read_bytes())
        version = taxonomy(raw)
        for f in read_facts(raw):
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
                # The day this became public, which is the only date a
                # point-in-time read may filter on.
                "as_of": doc.published_at,
            })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ("period_start", "period_end", "as_of"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    return df.drop_duplicates(IDENTITY, keep="first").reset_index(drop=True)
'''

README = """\
# {name}

A qanat project over real SEC filings. Two stages: what arrived, and what was
read out of it.

    export SEC_USER_AGENT="your name you@example.com"

    qanat check      # does the pipeline obey the stage contract?
    qanat mcp        # serve this project to your agent
    qanat schedule   # run every job on its clock, serving nothing

The SEC needs no account and no API key, only a contact address so it can tell
who is making the requests. Filings are cached under `data/documents`, so only
the first run goes to the network for them.

`qanat check` will warn that nothing reads `normalized.facts`. That is correct:
a metric is a query over the facts, not another stage.
"""

FILES = {
    "qanat.yaml": QANAT_YAML,
    "README.md": README,
    "steps/parse_facts.py": PARSE_FACTS_PY,
}


def write_project(target: Path, name: str, force: bool = False,
                  store: str | None = None) -> list[Path]:
    written = []
    store_line = f"store: {store}" if store else "store: ./data"
    yaml_body = QANAT_YAML.replace("store: ./data", store_line)
    files = {**FILES, "qanat.yaml": yaml_body}
    for rel, body in files.items():
        path = target / rel
        if path.exists() and not force:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body.replace("{name}", name))
        written.append(path)
    (target / "data").mkdir(exist_ok=True)
    return written
