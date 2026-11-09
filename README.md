<p align="center">
  <img src="https://raw.githubusercontent.com/fidetolabs/qanat/main/assets/hero.jpg" alt="A qanat cut open: shaft mouths and fields on the surface, the tunnel running beneath them, and a man walking it" width="560">
</p>

<h1 align="center">Qanat</h1>

<p align="center">
  <i>A data platform for processing metrics from filings.</i>
</p>

<p align="center">
  <a href="https://github.com/fidetolabs/qanat/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-a2e65d?style=flat-square"></a>
  <a href="https://github.com/fidetolabs/qanat/blob/main/pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-a2e65d?style=flat-square"></a>
  <a href="#cautions"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
</p>

## What Qanat is

Qanat is a data platform for processing metrics from filings. It reads company
filings, computes the metrics you define, and answers as of any past date.

What you came for is a **screen**: define a metric, set a threshold, get the
companies that pass. Every stock screener on the internet screens *today*. This
one screens **as of any past filing date, with no look-ahead** -- which is the
whole difference, and the reason for everything below.

**Filings are the subject, not a limitation of the plumbing.** The tables are
called `documents`, `facts` and `spans`, and the SEC is one connector among
five. What narrows is the promise: this reads filings and makes metrics, and
that is the thing it intends to be good at.

It does four things:

| | |
| --- | --- |
| **ingest** | pull data in from wherever it lives |
| **save** | keep it with its dates, the way it arrived |
| **transform** | clean it, and work out what you define from it |
| **serve** | hand it back to your agent over MCP |

A scheduler keeps the ingesting and transforming going. An ingestion job pulls on
whatever clock a schedule row gives it, and a transform starts when the table it
reads is ready.

## Why the dates matter

A company publishes its results weeks or months after the period they cover. So
if you ask what a company looked like in March 2022, you should only see what
was public in March 2022.

Qanat keeps two dates on every number: the period it covers, and the day it
became public. Ask about a past date and you get what was public by then.

## How it is put together

**One store.** Parquet files read by DuckDB. Point `store:` at a directory and
the tables are files in it. A `.duckdb` path also opens, as a single file, and
the paragraph below is why you should not want one.

**A directory is the default, and the reason is the second person.** A DuckDB
file takes an exclusive lock, so while one person is ingesting, a teammate
opening the same project is refused -- not slowed, refused. With parquet the
writer adds a file nobody is reading, so a reader is never blocked. The forty
filings below also take 7 MB as parquet against 18 MB in a DuckDB file.

A table is a directory of files, so landing a day of filings writes a day of
filings rather than the whole corpus again.

**Layers.** Data moves forward through them. The first layer holds what arrived
and nobody edits it. The ones after hold what you worked out. You name them.

**Transforms.** A `.sql` file, or a `.py` file with a `run(ctx)`. One says which
tables it reads and writes, and it can only read what it declared.

**Sources.** Two connectors: SEC EDGAR and Yahoo Finance. That is the pipeline
this is — the generic HTTP, SQL and CSV connectors shipped until 0.12.0 and were
declared by nothing.

**Metrics.** A formula with a name and a version. Shared, because
`price / diluted EPS` is arithmetic with no judgement in it. The threshold you
compare it against is yours and never lives in the project.

**The MCP server.** How you talk to it. Your agent is the screen. For the
person running Qanat there are also pages -- `qanat serve --app` -- to create
projects, ingest, and see what is held, running and computed.

## How to use it

You need Python 3.10 or newer.

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
export SEC_USER_AGENT="your name you@example.com"
qanat init my-research --demo && cd my-research
```

The demo pulls four real 10-K filings from SEC EDGAR and reads the numbers out
of them, in under a minute. There is no API key and no account: the SEC asks
only for a contact address so it can see who is making the requests.

Then point an agent at it:

```bash
claude mcp add qanat -- qanat mcp --scope research
```

Any MCP client works:

```json
{ "mcpServers": { "qanat": { "command": "qanat", "args": ["mcp"], "cwd": "/path/to/my-research" } } }
```

Now you can ask for things:

> *"What data is in here, and where did it come from?"*
>
> *"Which companies passed my rule in 2022?"*
>
> *"What did those companies do over the next year?"*

### Pick how much the agent can do

`--scope` sets which tools your agent gets. Each level includes the one before it.

| | Tools | What it can do |
| --- | --- | --- |
| `--scope data` | 14 | read the tables as they stood on a past date, list the companies it holds and read one, read the filings themselves |
| `--scope research` | 20 | and find filings on EDGAR, screen, and ask what window a screen can answer for |
| *(default)* | 28 | and ingest filings, subscribe to the ones that have not happened yet, run the graph |
| `--scope authoring` | 33 | and define metrics, and rewire the pipeline |

Every tool description is sent to the model on every request. A smaller scope
costs fewer tokens and gives the agent a shorter list to choose from, so pick
the smallest one that does your job.

**The default is not the widest one, on purpose.** The pipeline that turns a
filing into a metric is the same in every project, so an agent screening filings
has no use for the tools that rewire it — and carrying them means it can rewire
them by accident. `authoring` is for whoever runs the deployment: defining a
metric is their call, because a formula is the same number for every reader of
that Qanat and there is no such thing as a private one.

### The two things you actually do

Everything else is in service of these.

**Choose the filings.** `find_filings` asks EDGAR what exists — any of the 106
forms, no fetch, nothing stored — and labels each one with whether facts can be
derived from it. `ingest_filings` spends the fetch on the ones you pick.
`subscribe_filings` points the same selection forwards, and
`list_subscriptions` tells you whether anything is actually running it.

**Define what to measure.** `define_metric` writes a **transform job filling its
own table** — one metric, one table, in the metrics stage. It runs the formula
against your project first, so a broken one comes back as the database's own
message rather than as a screen that fails later, and runs it again straight
after so the metric answers immediately.

**There is no version to bump.** The table is keyed and never rewritten, so a
changed formula appends its values beside the old ones and each row still names
the execution that wrote it — and the execution recorded the definition's
digest, which *is* the version. An integer beside that digest was a second
version free to disagree with it, and measured, it did: halving a formula
appended 1,553 new values all still labelled version 1.

Only the 10-K and the 10-Q are parsed into the corpus today. Every other form is
found and labelled, never parsed, and `ingest_filings` says which pile a refused
form is in rather than failing quietly — because a product that ingests two forms
and crashes on the rest is not scoped, it is broken.

### The project files

A project is four files. **`qanat.yaml` is the one you write**, and nothing
rewrites it:

```yaml
# qanat.yaml -- yours
project: research
store: ./data            # a directory: parquet files, read by DuckDB

stages:
  - { id: raw,        kind: raw }         # what arrived, never edited
  - { id: normalized, kind: normalized }  # typed and de-duplicated

# Which column says when a row became public. Without this, a screen reads
# every row ever written, including the ones published after the date it is
# pretending to be.
time_columns:
  raw.documents: published_at

# corpus:, returns: and retention: live here too -- all architecture.
```

**`jobs.yaml` is written by the authoring tools.** It is a separate file because
the two kinds do not mix: an authoring verb that re-dumped one file took every
comment in it along with the change. A metric is a transform definition, so it
lands here too — there is no `metrics.yaml`.

```yaml
# jobs.yaml -- written by subscribe_filings, define_ingestion, define_transform
# What to fetch, and the one table it lands in. No cadence here.
ingestion_job_definitions:
  - id: filings
    to: [raw.documents]
    connector: sec
    key: [id]
    options:
      companies: [AAPL, MSFT]
      form: 10-K
      years: 5
      user_agent: ${SEC_USER_AGENT}

# Tables in, tables out, through a script. No cadence here either.
transform_job_definitions:
  - id: parse_facts
    from: [raw.documents]
    to:   [normalized.facts]
    script: transforms/parse_facts.py
    options: { reads: raw.documents }

# How often, as its own row. Several may point at one definition, so asking for
# one company daily does not rewrite a selection somebody else's weekly poll
# is using.
job_schedules:
  - { id: filings_daily, definition: filings, cron: "0 6 * * *" }
```

A transform in Python:

```python
def run(ctx):
    docs = ctx.read("raw.documents")   # only tables listed in `from`
    return df
```

`ctx.read()` refuses any table the transform did not declare, so a missing
dependency is an error you see instead of a wrong number you don't.

**You choose the layers and what they are called.** Qanat enforces four
properties and no names: a landed layer is never written by a transform, a
transform reads and writes only what it declared, the graph has no cycles, and
every row carries both dates. `raw` and `normalized` above are a worked example, not a
required shape.

**A pipeline stops at clean dated facts.** There is no metrics layer and no
results layer, because a metric is a query over the facts and a screen is a
call that returns a list. Neither is a table Qanat owes you, so neither needs
a stage to land in. `qanat check` warns that nothing reads `normalized.facts`,
and that warning is correct.

Run `qanat check` before anything else. It reads the file and tells you what is
wrong with it.

### Screening

One verb, one date or a window. A window of one is a single date, so the rows
come back the same shape either way and you never branch on which you asked
for.

Screening is a tool call, not a command: this is reached by an agent, and a
second door onto the same question is a second place the behaviour can differ.

```json
{"name": "screen", "arguments": {
  "clauses": [{"metric": "pe", "op": "lt", "value": 30}],
  "universe": ["AAPL", "MSFT", "NVDA"],
  "as_of": "2025-12-31"}}

{"name": "screen", "arguments": {
  "clauses": [{"metric": "pe", "metric_version": "95a0eaadebb0f3b0", "op": "lt", "value": 30},
              {"metric": "net_margin", "op": "gte", "value": 0.2}],
  "from": "2021-12-31", "to": "2025-12-31", "every": "1y"}}
```

**A filter is your named rule, and what crosses the wire is its clauses.** A
filter is not stored here — it is your research criteria rather than a financial
dataset, and a server with no identity has nowhere to keep one person's
preference. So there is no name to send: you hold the filter, which means you
already know which rule you just ran.

What comes back in `applied` is the half you could *not* have known:
`metric_version_used`, the definition digest each clause actually read. Hand it
back as a clause's `metric_version` to read the same answer again, and then
somebody editing a formula cannot change what your rule computed. It is a
digest rather than a number you maintain, because a digest moves when the
formula moves and a number has to be remembered.

**Because you keep the record, a screen hands you enough to keep one.** Each row
carries `values` — the numbers the verdict was reached on, for the companies that
failed as well as the ones that passed — and an `inputs` block names the
definition versions it actually read and the corpus range it ran over.
`inputs.id` is a digest of those inputs, so the same inputs give the same id
and a changed id means something underneath moved. An as-of date protects a
replay from a filer restating their numbers; it does not protect it from a parser
being fixed and the corpus reloaded, and that id is how you see the difference.

**`universe` bounds who is considered, and widens the answer as well as
narrowing it.** Pass a list of tickers or CIKs — a list, not the name of a
stored one, because which companies you care about is yours to keep. **Build it
from `list_companies`**, which is the index of every company this project holds
filings for.

A name you asked about that has no value for one of your metrics comes back as a
row that could not be evaluated, rather than being quietly absent — because
without a universe the subjects are whoever the metrics returned, and *"nobody
else qualified"* is not the same answer as *"I could not judge nine of them."*
A name this project does not hold **at all** is reported rather than dropped: a
list of fifty where ten are not held is a screen that quietly answered for
forty.

Entries may carry `from` and `to`, and then membership is point-in-time. A list
without them says so, because then it is today's membership applied to the past
and every number built on it is flattered by the names that survived.

**Ask `check_coverage` before asking for a window.** A read as of a date sees the rows
published on or before it, so before the oldest row there is nothing to read —
and a screen there returns a list of nobody, which is not the same answer as
nobody passing. A window that starts before the corpus is refused, with the
range it does hold.

Three things come back, and nothing else:

| | |
| --- | --- |
| **the list** | who passed, on each date |
| **the return** | per company, from its own filing to its next one |
| **the count** | how many could be evaluated, and how many could not |

**The count is there because a list is a claim.** A screen that quietly
dropped half the market has told you something false about who passed.

**Nothing is added up.** No average across years, no comparison against the
companies that failed, no equity curve. Adding returns together needs a
decision about how much of each company to hold, and that decision is yours
and is made outside this tool.

The returns run between each company's **own** dates. Costco files in October
and Walmart in March, so there is no shared calendar to price them on.

### Forty real filings to try it on

[`examples/sec-10k`](https://github.com/fidetolabs/qanat/tree/main/examples/sec-10k)
is eight companies over five years, downloaded from EDGAR: Apple, Microsoft,
Alphabet, Meta, NVIDIA, Walmart, Target and Costco. 43,104 tagged numbers and
904 document sections. Their fiscal years end in January, February, June,
August, September and December, so the two-date clock gets a real test rather
than a convenient one.

More in [docs/dataflow.md](https://github.com/fidetolabs/qanat/blob/main/docs/dataflow.md) for the whole pipeline end to
end, [docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md) for what `check_project` enforces,
[docs/words.md](https://github.com/fidetolabs/qanat/blob/main/docs/words.md) for what each word means, and
[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md) for the surface once you are using it.

## Cautions

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or
say so on [Discord](https://discord.gg/JUmwATScS8).

**Two data connectors, and that is the design rather than the progress.** SEC
EDGAR, which reads 10-K and 10-Q filings, and Yahoo Finance, which reads prices.
A generic HTTP, SQL and CSV connector shipped until 0.12.0 and nothing declared
one: being a fixed pipeline is what keeps the surface small enough to drive by
asking. More upstreams will be added one at a time, each finished before the
next is started.

**You write the formulas and pick the numbers.** Qanat works out what you asked
for and reports what came out. Choosing a threshold is your job.

**The dates protect a transform, not you.** A transform cannot see past its date. You and
your agent still can, while you are deciding what to try.

**It cannot place an order,** and no agent it serves ever will.

**Prices come from Yahoo, through `yfinance`.** That is not an API with a
contract behind it, and Yahoo's terms do not grant redistribution. Every row
carries `source: yfinance` so a later cleanup has something to act on, and it
is behind the `prices` extra rather than installed by default. It is also
unofficial: Yahoo changes its endpoints and the library catches up afterwards.

**Section detection is a heuristic.** A 10-K names every Item on its contents
page and again throughout the prose, so most matches for "Item 1A" are not the
heading. It gets all 904 sections in the bundled example right and it will meet
documents it has not seen. Every span carries the rule that found it, so you
can tell how much to trust one.

**Still missing:** industry grouping, so a ranking is against whatever list of
companies you hand it. Also backfills: `options.from` is where a cold table
starts, and there is no verb for filling in history behind what you already
have.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG and run it across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
