<p align="center">
  <img src="https://raw.githubusercontent.com/fidetolabs/qanat/main/assets/hero.jpg" alt="A qanat cut open: shaft mouths and fields on the surface, the tunnel running beneath them, and a man walking it" width="560">
</p>

<h1 align="center">Qanat</h1>

<p align="center">
  <i>A data platform for investment research. It ingests financial data sources and computes
  point-in-time financial metrics for valuation and screening.</i>
</p>

<p align="center">
  <a href="https://github.com/fidetolabs/qanat/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-a2e65d?style=flat-square"></a>
  <a href="https://github.com/fidetolabs/qanat/blob/main/pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-a2e65d?style=flat-square"></a>
  <a href="#status"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
</p>

## What this is

You want to find companies worth reading about. So you set a rule, like *price under 20 times
earnings*, and you want the list of companies that passed.

Qanat is the thing underneath that. It holds the data, it works out the numbers your rule needs, and
it hands you the list.

Three jobs:

1. **Load financial data.** Anything with a date on it. Company accounts, share prices, whatever you
   bring.
2. **Work out the metrics.** You write the formula. Qanat computes it for every company on every
   date. It does not ship a list of metrics and it does not pick your thresholds.
3. **Give you the companies that passed.** For today, or for any day in the past.

Your agent drives all of it over MCP. There is no screen in the box; your agent client is the screen.

## The one idea that matters

Imagine a company closes its books on 31 December. It does not publish the numbers that day. It
publishes them about two months later. Across all US annual reports, the middle case is **62 days**.

So on 15 January, nobody could read those numbers yet. They did not exist in public.

If your screen uses them on 15 January anyway, it is reading the future. The list it gives you looks
better than anything you could have actually picked. This mistake is the single most common reason a
backtest promises something real investing never delivers.

**Qanat puts two dates on every number:**

| | |
| --- | --- |
| the period date | the day the number is *about* (31 December) |
| the published date | the day it became *public* (early March) |

A screen run as of any past date reads only what was public on that date. Nothing else is visible to
it, so it cannot cheat.

Every stock screener on the internet screens **today**. This one screens **any day**, correctly.
That is the whole point of it.

## Try it in two minutes

You need Python 3.10 or newer.

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
qanat init my-research --demo && cd my-research
```

`--demo` builds a small working project and runs it, in about ten seconds. The data is made up, so
you need no API key and no network. It writes one metric, ranks companies on it, and reports what the
top of that ranking went on to do.

Leave `--demo` off and you get the same project with nothing run yet.

Qanat itself never calls the network. The only calls are the ones your own data sources make.

## Connect it to an agent

```bash
claude mcp add qanat -- qanat mcp --scope research
```

Any MCP client works:

```json
{ "mcpServers": { "qanat": { "command": "qanat", "args": ["mcp"], "cwd": "/path/to/my-research" } } }
```

Then ask for things in plain language:

- **"What data is in this project, and where did it come from?"** It traces any table back to its
  source and answers from the actual rows.
- **"Add a metric for price over earnings."** It tells you what your data covers first, then asks you
  for the parts it must not guess.
- **"Which companies passed my rule in 2022?"** It runs the screen as of 2022's publication dates and
  lists them.
- **"What did that list go on to do?"** It reports each company's return from its own publication
  date to the next year's.

It never tells you a company is a good buy. You write the rule; it reports what came out.

## Scopes: how much the agent can do

`--scope` picks which tools your agent gets. Each level contains the one before it.

| | What it can do |
| --- | --- |
| `--scope data` | read the tables, including as they stood on a past date |
| `--scope research` | and read a step, run a screen, read the result |
| *(default, `full`)* | and write steps, sources and universes |

Every tool definition is sent to the model on every single request. A narrow scope costs fewer tokens
and leaves the agent a shorter list to choose from, so it chooses better. **Pick the smallest one
that does your job.**

Full table in **[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)**.

## Running it for something not on this machine

```bash
qanat mcp --http --port 8421 --scope research --token "$QANAT_MCP_TOKEN"
```

This speaks MCP's Streamable HTTP transport on `/mcp`. The scope is fixed by the flag that started
the server, so nothing in a request can widen it.

One process serves one project, because the data store takes one writer. It binds to `127.0.0.1`
unless you say otherwise, and it refuses any other address unless you pass a token.

## How a project is written

Everything lives in one `qanat.yaml` file.

```yaml
project: equity
store: ./data/qanat.duckdb

universes:                         # which companies the screen may look at
  - id: sp100
    symbols: ./universes/sp100.csv

stages:                            # the order here is the order data moves
  - { id: raw,        kind: raw }
  - { id: normalized, kind: normalized }
  - { id: features,   kind: features }
  - { id: weights,    kind: weights }
  - { id: pnl,        kind: pnl }

sources:
  - id: prices
    to: [raw.daily_prices]
    connector: rest
    options:
      url: https://api.example.com/v1/bars

steps:
  - id: normalize                  # clean it up
    from: [raw.daily_prices]
    to:   [normalized.prices]
    script: steps/normalize.sql

  - id: momentum                   # a metric: one number per company per date
    from: [normalized.prices]
    to:   [features.momentum]
    script: steps/momentum.py
    options: { reads: normalized.prices, lookback: 20 }

  - id: pick_top                   # the screen: rank on the metric, keep the top few
    from: [features.momentum]
    to:   [weights.momentum]
    script: steps/pick_top.py
    universe: sp100
    options: { reads: features.momentum, column: momentum, top_n: 4 }
    rebalance: 20d
```

A step is either a `.sql` file or a `.py` file with a `run(ctx)`:

```python
def run(ctx):
    bars = ctx.read("normalized.prices")   # only tables this step listed in `from`
    return df
```

`ctx.read()` refuses any table the step did not declare. So a forgotten dependency is an error you
see, not a wrong number you do not.

**Data moves forward and never back.** `raw` keeps it exactly as it arrived and is never edited.
`normalized` types and de-duplicates it. `features` holds what you measure. The last two stages hold
the screen's output and what it returned.

`qanat check` refuses a project that breaks these rules before anything runs. The rules are in
**[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)**.

## Screening the past

```bash
qanat backtest --from 2015-01-01 --to 2024-12-31 --step pick_top
qanat report 14
```

This walks the period one date at a time. On each date, every step sees only the rows that were
public then, so nothing in the run can read the future.

**What it reports today, and what is changing.** Today it treats the selection as a portfolio: it
holds it, charges fees and slippage, and gives you one net number at the end. That is a backtesting
engine's answer, and it is being replaced.

What it is moving to is one row per date: which companies passed, and what each one returned by the
next date, with nothing added together. Adding returns up means deciding how much of each company to
hold, and that decision is yours, not the platform's.

More in **[docs/backtest.md](https://github.com/fidetolabs/qanat/blob/main/docs/backtest.md)**.

## What ships with it

**Four connectors.** `rest` for an HTTP endpoint, `sql` for anything SQLAlchemy reaches, `csv` for a
path or a URL, and `synthetic` for a fake market you can run before you have any API key.

**The store** is a DuckDB file, or Postgres on localhost.

**Five examples.** `examples/filings-sample` is the shape this is built for: a filings panel where
every row carries both dates. Its README is about which of the two is the clock. Its numbers are
invented. `examples/fx-bundled` is the other kind, with real data in the repo: 27 years of ECB
exchange rates, 128 KB, no key needed.

## Status

Beta. It does what this page says on my own work, and few other people have run it. If something
breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or say so on
[Discord](https://discord.gg/JUmwATScS8).

**MCP is the only interface.** You install the engine, a CLI to operate it, and the MCP server.
There is no web screen and no second HTTP API.

**Be clear about what is not here yet.**

- **Nothing in the package reads a company filing.** There are four generic connectors and no SEC
  parser. Loading real company accounts is yours to write, or point it at a feed you already have.
- **No industry grouping.** Comparing a company only against its real peers needs a classification
  that is itself point-in-time, and that does not exist here. Today a ranking is against whatever
  universe you declare.
- **No backfills and no incremental windows.**
- **No trading.** Qanat produces a list. **It does not place an order**, and no agent it serves ever
  will.

**Two names are changing.** The last two pipeline stages are still called `weights` and `pnl`, from
when this was built as a backtesting engine. It is a screening platform now, so those names describe
the old product. The rename is in progress and the stage contract will move with it.

**One real limit.** The point-in-time views stop a *step* seeing the future. Nothing stops *you*, or
your agent, reading past a date while deciding what to try. The data cannot cheat; a person still
can.

**What this is not.** Qanat runs one shape of pipeline. Airflow, Dagster and Prefect handle arbitrary
DAGs and distributed execution, and they are the right tools for that.

Issues and pull requests welcome. See
**[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md)**.

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
