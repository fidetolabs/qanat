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
# The line between them is **authorship**, not derivation -- the same line as
# fact-versus-metric: a fact is what they said, a metric is what you chose.
stages:
  - id: raw
    kind: raw
    description: landed exactly as it arrived, never edited
  - id: normalized
    kind: normalized
    description: unpacking what arrived, with no choice of your own
  - id: features
    kind: features
    description: your judgement, but not one number per subject per date
  # **The registry.** Every table in here is a metric, so nothing keeps a list:
  # "what metrics exist" is what the definitions write. One metric is one
  # table, because metrics derive from each other -- a rank reads the metric it
  # ranks, and with one shared table that is a transform reading the table it
  # writes, which `check` refuses and the run order cannot sequence.
  - id: metrics
    kind: metrics
    description: your judgement, one number per subject per date -- screenable

# ------------------------------------------------------------------- the clock --
# A filing has two dates: the period it describes, and the day it became
# public. This names the second one. Point it at the first and every screen
# reads numbers weeks before anybody could have known them.
time_columns:
  raw.documents: published_at
  normalized.facts: as_of
  # One clock per metric table, because one metric is one table.
  metrics.net_margin: date

# ------------------------------------------------------------------ the corpus --
# Which tables hold the documents and the numbers pulled out of them. The
# reading tools need to know where to look, and the platform does not name
# layers -- so the project says. Without this they are simply not offered.
#
# **There is no `metrics:` entry.** A metric is not one table this names: one
# metric is one table in the metrics stage, and the stage is the registry.
corpus:
  documents: raw.documents
  facts: normalized.facts

"""

#: Tool-written, and said out loud in the file itself: a definition, a
#: cadence and a formula are things an authoring tool writes, and a tool
#: that re-dumps a file cannot keep the comments somebody put in it. So they
#: are not in `qanat.yaml`, which is the one file nothing here rewrites.
JOBS_YAML = """\
# --------------------------------------------------------- the ingestion jobs --
# What to fetch, and the one table it lands in. **No cadence here** -- how often
# this runs is a `job_schedules:` row pointing at it, below, so one definition
# can carry a daily and a weekly without being copied.
#
# `key:` names the columns that identify one row. A filing that arrives again
# with the same values is not stored twice; one that arrives with a changed
# value is written beside the old row rather than over it. There is no
# `mode: replace` -- it rewrote this table to only what the selection returned,
# so every filing ingested outside that list was deleted by a run that
# reported ok.
ingestion_job_definitions:
  - id: filings
    to: [raw.documents]
    connector: sec          # swap for rest, sql or csv when you have a feed
    key: [id]
    options:
      companies: [AAPL, MSFT]
      form: 10-K
      years: 2
      # The SEC refuses requests that carry no contact. Put yours here, or
      # export SEC_USER_AGENT and leave this line alone.
      user_agent: ${SEC_USER_AGENT}
      cache: ./data/documents

# -------------------------------------------------------------- the transforms --
# Tables in, tables out, through a script. n:m -- `from` and `to` are both
# lists. No cadence here either.
transform_job_definitions:
  - id: parse_facts
    from: [raw.documents]
    to:   [normalized.facts]
    script: transforms/parse_facts.py
    options:
      reads: raw.documents

  # ------------------------------------------------------------- the metric --
  # **A metric is a transform job writing its own table.** The starter set is
  # one formula, so the product's own verb works on the day you install -- and
  # deliberately not a metric library. Call `define_metric` to write your own
  # beside it; it writes a definition exactly like this one.
  #
  # `key:` is what makes it a metric rather than an ordinary derived table. An
  # ordinary transform rewrites its output, which is right -- it is a function
  # of what is upstream. A metric value is not: it is what somebody was shown,
  # so a corrected input writes a new row beside the old one.
  #
  # There is no `version:`. The version is this definition's digest, recorded
  # on the execution that ran, and `lineage` resolves a value to it. An integer
  # beside it would be a second version free to disagree.
  - id: net_margin
    from: [normalized.facts]
    to:   [metrics.net_margin]
    script: transforms/metrics/net_margin.sql
    key:  [subject, date]
    options:
      unit: ratio
      description: >
        net income over revenue, for the most recent full year each filer had
        reported as of each filing date. Filers do not agree on a revenue tag --
        NVIDIA uses Revenues, Apple uses the contract tag -- so both are
        accepted and the contract one wins when a filer tags both.

# --------------------------------------------------------------- the schedules --
# A cadence, as a row. Each one points at one definition by id and holds nothing
# else: what to fetch is the definition's. Several may point at the same
# definition, and a definition with none is ordinary -- it runs when you ask.
#
# Nothing is on a clock in a fresh project. Add a row here, or call
# `subscribe_filings` and one is written for you:
#
# job_schedules:
#   - id: filings_daily
#     definition: filings
#     cron: "0 6 * * *"

"""

NET_MARGIN_SQL = """\
-- net_margin -- the starter metric, as a series.
--
-- **One row per subject per filing date.** `date` is the day the value became
-- true, which is what makes this a series rather than an answer to one call:
-- `time_columns` filters it exactly as it filters the facts underneath.
--
-- It used to take the date as a parameter -- `as_of <= :as_of`, collapsing to
-- one row per company -- and store nothing. That kind is gone. With nothing
-- stored a screen had to run the formula, and a formula with no date to bind
-- ran over every row, so a screen could return a figure that was not public
-- yet. A stored row also names the execution that wrote it; a computed one has
-- nothing to name.
WITH annual AS (
  SELECT subject, concept, value, as_of, period_end
  FROM normalized__facts
  WHERE dims = '{}' AND NOT is_instant
    AND date_diff('day', period_start, period_end) BETWEEN 350 AND 380
), ni AS (
  SELECT subject, as_of, value,
         row_number() OVER (PARTITION BY subject, as_of
                            ORDER BY period_end DESC) AS rn
  FROM annual WHERE concept = 'us-gaap:NetIncomeLoss'
), rev AS (
  -- Both revenue tags are accepted and the contract one wins, because a filer
  -- tagging both means the same number twice.
  SELECT subject, as_of, value,
         row_number() OVER (PARTITION BY subject, as_of
                            ORDER BY period_end DESC,
                            CASE concept
                              WHEN 'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax' THEN 0
                              ELSE 1 END) AS rn
  FROM annual WHERE concept IN (
    'us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax',
    'us-gaap:Revenues')
), reported AS (
  SELECT ni.subject, ni.as_of, ni.value / NULLIF(rev.value, 0) AS value
  FROM ni JOIN rev
    ON rev.subject = ni.subject AND rev.as_of = ni.as_of AND rev.rn = 1
  WHERE ni.rn = 1
),
-- Every day anything was filed. A 10-Q carries no annual figure, so the value
-- holds flat from the last filing that reported one, which is what
-- `as_of <= :as_of` used to produce one date at a time.
filing_days AS (SELECT DISTINCT subject, as_of FROM normalized__facts)
SELECT d.subject,
       CAST(d.as_of AS DATE) AS date,
       r.value               AS value
FROM filing_days d
ASOF JOIN reported r
  ON d.subject = r.subject AND d.as_of >= r.as_of
WHERE r.value IS NOT NULL
"""

PARSE_FACTS_PY = '''\
"""Every tagged number in every filing, as rows of `facts`.

`qanat.xbrl` does the reading. It knows inline XBRL and nothing about the SEC,
so this transform is just the join: open the gzip, parse it, and stamp each fact
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

    qanat.yaml      what you write and mean to keep -- stages, the clock, the corpus
    jobs.yaml       what to fetch, what to transform, and how often. Tool-written
    transforms/      the scripts the jobs run, metrics included

**Nothing rewrites `qanat.yaml`.** The other two are written by authoring tools,
and a tool that re-dumps a file cannot keep the comments somebody put in it --
which is why the file holding your notes is not one of them.

    export SEC_USER_AGENT="your name you@example.com"

    qanat check      # does the pipeline obey the stage contract?
    qanat mcp        # serve this project to your agent
    qanat schedule   # fire every schedule on its clock, serving nothing

Nothing is on a clock yet. A cadence is a `job_schedules:` row pointing at a
definition by id, and `subscribe_filings` writes one for you -- so asking for
one company daily does not rewrite the selection somebody else's weekly poll
is using.

The SEC needs no account and no API key, only a contact address so it can tell
who is making the requests. Filings are cached under `data/documents`, so only
the first run goes to the network for them.

`qanat check` will warn that nothing reads `normalized.facts`. That is correct:
a metric is a query over the facts, not another stage.
"""

FILES = {
    "qanat.yaml": QANAT_YAML,
    "jobs.yaml": JOBS_YAML,
    "transforms/metrics/net_margin.sql": NET_MARGIN_SQL,
    "README.md": README,
    "transforms/parse_facts.py": PARSE_FACTS_PY,
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
