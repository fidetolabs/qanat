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
  <a href="#the-limits"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
</p>

## What Qanat is

Qanat reads company filings, computes the metrics you define, and answers as of
any past date.

What you came for is a **screen**: define a metric, set a threshold, get the
companies that pass. Every stock screener on the internet screens *today*. This
one screens **as of any past filing date, with no look-ahead.**

A company publishes its results weeks or months after the period they cover, so
Qanat keeps two dates on every number: the period it covers, and the day it
became public. Ask about March 2022 and you get what was public in March 2022.

It does four things. **ingest** pulls data in, **save** keeps it with its dates
the way it arrived, **transform** cleans it and works out what you defined, and
**serve** hands it back to your agent over MCP. A scheduler keeps the first and
third going: an ingestion job pulls on whatever clock a schedule row gives it,
and a transform starts when the table it reads is ready.

**Not investment advice.** Qanat computes the formula you wrote and reports the
number that came out. What you do with it is your decision and your risk.

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

`qanat mcp` serves over stdio, as your client's child, and the folder you start
it in is the project. `qanat serve` is the HTTP server over a folder of
projects, and `--mcp`, `--app` and `--schedule` are doors you open on it. The
pages behind `--app` are for the person running Qanat: create projects, ingest,
and see what is held, running and computed.

### Pick how much the agent can do

`--scope` sets which tools your agent gets. Each level includes the one before.

| | Tools | What it adds |
| --- | --- | --- |
| `--scope data` | 17 | read the tables as they stood on a past date, list the companies it holds and read one, read the filings themselves, choose the project |
| `--scope research` | 24 | find filings on EDGAR, screen, read the saved filters, ask what window a screen can answer for |
| `--scope full` *(default)* | 33 | ingest filings, subscribe to the ones that have not happened yet, name a universe, run the graph, create a project |
| `--scope authoring` | 41 | define metrics and transforms, save filters, rewire the pipeline |

Every tool description is sent to the model on every request, so a smaller
scope costs fewer tokens and gives the agent a shorter list to choose from.

**The default is not the widest one, on purpose.** The pipeline that turns a
filing into a metric is the same in every project, so an agent screening
filings has no use for the tools that rewire it, and carrying them means it can
rewire them by accident. `authoring` is for whoever runs the deployment:
defining a metric is their call, because a formula is the same number for every
reader of that Qanat and there is no such thing as a private one.

### The two things you actually do

**Choose the filings.** `find_filings` asks EDGAR what exists -- any of the 106
forms, no fetch, nothing stored -- and labels each one with whether facts can be
derived from it. `ingest_filings` spends the fetch on the ones you pick.
`subscribe_filings` points the same selection forwards, and
`list_subscriptions` tells you whether anything is actually running it.

Only the 10-K and the 10-Q are parsed into the corpus today. Every other form is
found and labelled, never parsed, and `ingest_filings` says which pile a refused
form is in rather than failing quietly.

**Define what to measure.** `define_metric` writes a transform job filling its
own table: one metric, one table, in the metrics stage, which is the registry
`list_metrics` reads. It runs the formula against your project first, so a
broken one comes back as the database's own message rather than as a screen
that fails later.

**There is no version to bump.** The table is keyed and never rewritten, so a
changed formula appends its values beside the old ones and each row still names
the execution that wrote it -- and the execution recorded the definition's
digest, which *is* the version. An integer beside that digest was a second
version free to disagree with it, and measured, it did: halving a formula
appended 1,553 new values all still labelled version 1.

### Screening

One verb, one date or a window. A window of one is a single date, so the rows
come back the same shape either way.

```json
{"name": "screen_companies", "arguments": {
  "clauses": [{"metric": "pe", "op": "lt", "value": 30}],
  "companies": ["AAPL", "MSFT", "NVDA"],
  "as_of": "2025-12-31"}}

{"name": "screen_companies", "arguments": {
  "clauses": [{"metric": "pe", "metric_version": "95a0eaadebb0f3b0", "op": "lt", "value": 30},
              {"metric": "net_margin", "op": "gte", "value": 0.2}],
  "from_universe": "sp500",
  "from": "2021-12-31", "to": "2025-12-31", "every": "1y"}}
```

**The rule and the list each have two ways in, and the split is who owns it.**
Your own rule crosses as `clauses` and your own names as `companies`, because a
server with no identity has nowhere to keep one person's preference. A rule
this deployment owns is saved in `filters.yaml` by `save_filter` and run by id
as `filter`; a list it owns is a csv under `./universes/` from `define_universe`
and named as `from_universe`. One or the other each time, never both.

**Build `companies` from `list_companies`**, which is the index of every company
this project holds filings for. A name you asked about that has no value for one
of your metrics comes back as a row that could not be evaluated rather than
quietly absent, and a name this project does not hold at all is reported rather
than dropped: a list of fifty where ten are not held is a screen that quietly
answered for forty. Entries may carry `from` and `to`, and then membership is
point-in-time rather than today's membership applied to the past.

**Ask `check_coverage` before asking for a window.** Before the oldest row there
is nothing to read, and a screen there returns a list of nobody, which is not
the same answer as nobody passing. A window that starts before the corpus is
refused, with the range it does hold.

Three things come back: **the list**, who passed on each date; **the return**,
per company from its own filing to its next one; and **the count**, how many
could be evaluated and how many could not. The count is there because a list is
a claim, and a screen that quietly dropped half the market has told you
something false about who passed. The returns run between each company's *own*
dates, because Costco files in October and Walmart in March.

Each row also carries `values`, the numbers the verdict was reached on, for the
companies that failed as well as the ones that passed. `applied` echoes
`metric_version_used`, the definition digest each clause actually read: hand it
back as a clause's `metric_version` and somebody editing a formula cannot change
what your rule computed. `inputs.id` is a digest of the definition versions and
the corpus range the run read, so a changed id means something underneath moved.
An as-of date protects a replay from a filer restating their numbers; it does
not protect it from a parser being fixed and the corpus reloaded, and that id is
how you see the difference.

**Nothing is added up.** No average across years, no comparison against the
companies that failed, no equity curve. Adding returns together needs a decision
about how much of each company to hold, and that decision is yours.

### Forty real filings to try it on

[`examples/sec-10k`](https://github.com/fidetolabs/qanat/tree/main/examples/sec-10k)
is eight companies over five years, downloaded from EDGAR: Apple, Microsoft,
Alphabet, Meta, NVIDIA, Walmart, Target and Costco. 43,104 tagged numbers and
904 document sections. Their fiscal years end in January, February, June,
August, September and December, so the two-date clock gets a real test rather
than a convenient one.

A project is `qanat.yaml`, which you write and nothing rewrites, plus
`jobs.yaml` and `filters.yaml`, which the tools write as you use them.
`qanat init` explains all three in the project it scaffolds, and `qanat check`
holds the result against the stage contract. More in
[docs/dataflow.md](https://github.com/fidetolabs/qanat/blob/main/docs/dataflow.md)
for the whole pipeline end to end,
[docs/flows.md](https://github.com/fidetolabs/qanat/blob/main/docs/flows.md) for
why each tool exists,
[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)
for the stage rules `check_project` enforces,
[docs/words.md](https://github.com/fidetolabs/qanat/blob/main/docs/words.md) for
what each word and each file holds, and
[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)
for the surface once you are using it.

## Why Qanat

**A screen that cannot name its date is not evidence.** The whole product is the
two-date clock: a transform cannot see past the date it is run for, so a rule
tested on 2022 is tested on what 2022 knew.

**Filings are the subject, not a limitation of the plumbing.** The tables are
called `documents`, `facts` and `spans`, and SEC EDGAR is one of the two
connectors, Yahoo Finance the other. A generic HTTP, SQL and CSV connector
shipped until 0.12.0 and nothing declared one. Being a fixed pipeline is what
keeps the surface small enough to drive by asking, and that surface is the
product: your agent is the screen, and there is no second door onto the same
question that could answer differently.

**One store, and it is a directory of parquet files read by DuckDB.** A DuckDB
file takes an exclusive lock, so while one person is ingesting, a teammate
opening the same project is refused, not slowed. With parquet the writer adds a
file nobody is reading. The forty filings above also take 7 MB as parquet
against 18 MB in a DuckDB file, and landing a day of filings writes a day rather
than the whole corpus again. A `.duckdb` path still opens if you want one.

**A metric is shared and a threshold is not.** `price / diluted EPS` is
arithmetic with no judgement in it, so it belongs to the deployment. What you
compare it against is research criteria, so it never lives in the project unless
you put it there yourself.

### The limits

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or
say so on [Discord](https://discord.gg/JUmwATScS8).

**It cannot place an order,** and no agent it serves ever will.

**The dates protect a transform, not you.** A transform cannot see past its
date. You and your agent still can, while you are deciding what to try. You
write the formulas and pick the numbers, and choosing a threshold is your job.

**Prices come from Yahoo, through `yfinance`.** That is not an API with a
contract behind it, and Yahoo's terms do not grant redistribution. Every row
carries `source: yfinance` so a later cleanup has something to act on, and it is
behind the `prices` extra rather than installed by default. It is also
unofficial: Yahoo changes its endpoints and the library catches up afterwards.

**Section detection is a heuristic.** A 10-K names every Item on its contents
page and again throughout the prose, so most matches for "Item 1A" are not the
heading. It gets all 904 sections in the bundled example right and it will meet
documents it has not seen. Every span carries the rule that found it, so you can
tell how much to trust one.

**Still missing:** industry grouping, so a ranking is against whatever list of
companies you hand it. Also backfills: `options.from` is where a cold table
starts, and there is no verb for filling in history behind what you already
have. More upstreams will be added one at a time, each finished before the next
is started.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG and run it across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
