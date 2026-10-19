<p align="center">
  <img src="https://raw.githubusercontent.com/fidetolabs/qanat/main/assets/hero.jpg" alt="A qanat cut open: shaft mouths and fields on the surface, the tunnel running beneath them, and a man walking it" width="560">
</p>

<h1 align="center">Qanat</h1>

<p align="center">
  <i>A data platform for investment research.</i>
</p>

<p align="center">
  <a href="https://github.com/fidetolabs/qanat/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-a2e65d?style=flat-square"></a>
  <a href="https://github.com/fidetolabs/qanat/blob/main/pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-a2e65d?style=flat-square"></a>
  <a href="#cautions"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
</p>

## What Qanat is

Qanat is a data platform for investment research. You load company data into it,
write the formulas you care about, and ask for the numbers as they stood on any
past date.

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

**One store.** A DuckDB file, or Postgres on localhost.

**Layers.** Data moves forward through them. The first layer holds what arrived
and nobody edits it. The ones after hold what you worked out.

**Steps.** A `.sql` file, or a `.py` file with a `run(ctx)`. A step says which
tables it reads and writes, and it can only read what it declared.

**Sources.** Four connectors: HTTP, SQL, CSV, and a fake market you can use
before you have any API key.

**The MCP server.** How you talk to it. There is no website. Your agent is the
screen.

## How to use it

You need Python 3.10 or newer.

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
qanat init my-research --demo && cd my-research
```

The demo builds a small project and runs it in about ten seconds. The data is
made up, so you need no API key and no network.

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
| `--scope data` | 6 | read the tables, including as they stood on a past date |
| `--scope research` | 13 | and read a step, run a replay, read the result |
| *(default)* | 21 | and write steps, sources and universes |

Every tool description is sent to the model on every request. A smaller scope
costs fewer tokens and gives the agent a shorter list to choose from, so pick
the smallest one that does your job.

### The project file

Everything is in one `qanat.yaml`.

```yaml
project: equity
store: ./data/qanat.duckdb

stages:
  - { id: raw,        kind: raw }         # what arrived, never edited
  - { id: normalized, kind: normalized }  # typed and de-duplicated
  - { id: features,   kind: features }    # what you measure
  - { id: weights,    kind: weights }
  - { id: pnl,        kind: pnl }

sources:
  - id: prices
    to: [raw.daily_prices]
    connector: rest
    options: { url: https://api.example.com/v1/bars }

steps:
  - id: normalize
    from: [raw.daily_prices]
    to:   [normalized.prices]
    script: steps/normalize.sql

  - id: momentum
    from: [normalized.prices]
    to:   [features.momentum]
    script: steps/momentum.py
    options: { reads: normalized.prices, lookback: 20 }
```

A step in Python:

```python
def run(ctx):
    bars = ctx.read("normalized.prices")   # only tables listed in `from`
    return df
```

`ctx.read()` refuses any table the step did not declare, so a missing dependency
is an error you see instead of a wrong number you don't.

Run `qanat check` before anything else. It reads the file and tells you what is
wrong with it.

### Running over history

```bash
qanat backtest --from 2026-01-01 --to 2026-09-30 --step sort_on_trailing_return
```

This walks the window one date at a time. On each date every step sees only the
rows that were public then. You get one row per date, with the number of dates
next to the result, because a figure from four dates and a figure from four
hundred are different claims.

More in [docs/backtest.md](https://github.com/fidetolabs/qanat/blob/main/docs/backtest.md),
[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)
and [docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md).

## Cautions

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or
say so on [Discord](https://discord.gg/JUmwATScS8).

**You bring the data.** There is no SEC filing parser in here. You write the
connector, or point it at a feed you already have.

**You write the formulas and pick the numbers.** Qanat works out what you asked
for and reports what came out. Choosing a threshold is your job.

**The dates protect a step, not you.** A step cannot see past its date. You and
your agent still can, while you are deciding what to try.

**It cannot place an order,** and no agent it serves ever will.

**Still missing:** industry grouping, so a ranking is against whatever list of
companies you hand it. Also backfills and incremental updates.

**Two layer names are out of date.** The last two stages are still called
`weights` and `pnl`, from when this was built as a backtesting engine. The code
is a screening platform now and the names have not caught up.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG and run it across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
