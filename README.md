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

A scheduler keeps the ingesting and transforming going. A source pulls on its
own clock, and a step starts when the table it reads is ready.

## Why the dates matter

A company publishes its results weeks or months after the period they cover. So
if you ask what a company looked like in March 2022, you should only see what
was public in March 2022.

Qanat keeps two dates on every number: the period it covers, and the day it
became public. Ask about a past date and you get what was public by then.

## How it is put together

**One store.** Parquet files read by DuckDB. Point `store:` at a directory and
the tables are files in it; point it at a `.duckdb` path for a single file, or a
`postgresql://` DSN for a server.

**A directory is the default, and the reason is the second person.** A DuckDB
file takes an exclusive lock, so while one person is ingesting, a teammate
opening the same project is refused -- not slowed, refused. With parquet the
writer adds a file nobody is reading, so a reader is never blocked. The forty
filings below also take 6.7 MB as parquet against 37 MB in a DuckDB file.

**Layers.** Data moves forward through them. The first layer holds what arrived
and nobody edits it. The ones after hold what you worked out. You name them.

**Steps.** A `.sql` file, or a `.py` file with a `run(ctx)`. A step says which
tables it reads and writes, and it can only read what it declared.

**Sources.** Five connectors: SEC EDGAR, Yahoo Finance, HTTP, SQL and CSV.

**Metrics.** A formula with a name and a version. Shared, because
`price / diluted EPS` is arithmetic with no judgement in it. The threshold you
compare it against is yours and never lives in the project.

**The MCP server.** How you talk to it. There is no website. Your agent is the
screen.

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
| `--scope data` | 12 | read the tables as they stood on a past date, and read the filings |
| `--scope research` | 15 | and read a step, and screen |
| *(default)* | 23 | and write steps, sources and universes |

Every tool description is sent to the model on every request. A smaller scope
costs fewer tokens and gives the agent a shorter list to choose from, so pick
the smallest one that does your job.

### The project file

Everything is in one `qanat.yaml`.

```yaml
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

sources:
  - id: filings
    to: [raw.documents]
    connector: sec
    options:
      companies: [AAPL, MSFT]
      form: 10-K
      years: 5
      user_agent: ${SEC_USER_AGENT}

steps:
  - id: parse_facts
    from: [raw.documents]
    to:   [normalized.facts]
    script: steps/parse_facts.py
    options: { reads: raw.documents }
```

A step in Python:

```python
def run(ctx):
    docs = ctx.read("raw.documents")   # only tables listed in `from`
    return df
```

`ctx.read()` refuses any table the step did not declare, so a missing dependency
is an error you see instead of a wrong number you don't.

**You choose the layers and what they are called.** Qanat enforces four
properties and no names: a landed layer is never written by a step, a step
reads and writes only what it declared, the graph has no cycles, and every row
carries both dates. `raw` and `normalized` above are a worked example, not a
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

```bash
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --as-of 2025-12-31
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --from 2021-12-31 --to 2025-12-31
```

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

More in [docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md),
[docs/words.md](https://github.com/fidetolabs/qanat/blob/main/docs/words.md)
and [docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md).

## Cautions

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or
say so on [Discord](https://discord.gg/JUmwATScS8).

**One connector so far.** SEC EDGAR, and it reads 10-K filings. Anything else
you bring yourself through the HTTP, SQL or CSV connectors. More will be added
one at a time, each finished before the next is started.

**No prices.** A filing does not contain one, and nothing in here fetches one
yet. So you can screen on what a company reported and you cannot yet ask what
the list did next.

**You write the formulas and pick the numbers.** Qanat works out what you asked
for and reports what came out. Choosing a threshold is your job.

**The dates protect a step, not you.** A step cannot see past its date. You and
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
companies you hand it. Also backfills and incremental updates.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG and run it across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
