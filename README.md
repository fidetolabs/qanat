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
  <a href="#status"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
</p>

## What it is

Qanat is a **data platform for investment research**.

It does four things:

| | |
| --- | --- |
| **ingest** | pull data in from wherever it lives |
| **save** | keep it, dated, exactly as it arrived. The same data arriving twice is stored once, so a re-run is always safe |
| **transform** | clean it, type it, and compute what you define from it |
| **serve** | hand it back, including as it stood on a past date |

And it has its **own orchestrator** that runs those four and checks each one did what it should.

That part is not an extra. A source pulls on its own clock and a step waits for the table it reads,
so something has to hold the order. And a job that failed quietly looks exactly like a job that had
nothing to do. Only the thing that knew the job was due can tell those apart. Without it, a data set
loses a day and nobody notices.

What it is **for** is investment research: company accounts, prices, anything with a date on it.
Qanat computes the metrics you define over that data, for every company, for any date. Valuation
metrics and screening metrics are just metrics you defined.

What it is **not**: it does not define metrics for you, does not hold your thresholds, does not pick
companies, and does not trade. Those belong to you and to whatever you point at it.

## The dates are the point

Company results come out a while after the period they cover. Before that day, nobody could read
them.

So Qanat puts **two dates** on every number: the period it covers, and the day it became public.

Ask for a metric as of 2022 and you get it computed from what was public in 2022. Not from numbers
that arrived in 2023. Without that, every answer about the past is quietly better than anything you
could have acted on.

This is the one thing in Qanat that is hard to add later, and it is why the platform exists rather
than a folder of spreadsheets.

## Try it

Needs Python 3.10 or newer.

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
qanat init my-research --demo && cd my-research
```

That builds a small working project and runs it, in about ten seconds. The data is made up, so you
need no API key and no network.

## Use it from your agent

```bash
claude mcp add qanat -- qanat mcp --scope research
```

Any MCP client works. Then just ask:

> *"What data is in here, and where did it come from?"*
>
> *"Add a metric for price over earnings."*
>
> *"Which companies passed my rule in 2022?"*
>
> *"What did those companies do over the next year?"*

There is no website and no dashboard. Your agent is the screen.

## What Qanat keeps, and what it does not

**Qanat keeps what everyone shares.**

- Your data, in layers you name. A source lands it as it arrived; steps you write clean and type it.
  You choose the layers; Qanat ships no fixed set of them.
- Your metric definitions, with versions, so an old answer still means something.
- The computed metric values. A metric is the same number for everybody who asks, so it is worked out
  once and kept.

**Your client keeps what is yours alone.** Thresholds. Target rates. Which companies you watch. Your
saved work.

That split is not a preference. Qanat cannot tell two callers apart, so anything belonging to one
person cannot be stored in it correctly. Your agent, or a hosted service, is where that lives.

**So screening happens on your side.** Qanat serves the values. Your client compares them to your
numbers and gets the list.

The yaml, the step contract and the four rules Qanat enforces are in
**[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)**.
Tool-by-tool reference is in
**[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)**.

## What comes in the box

| | |
| --- | --- |
| **Connectors** | HTTP, SQL, CSV, and a fake market so you can play before you have any API key |
| **Store** | a DuckDB file, or Postgres on localhost |
| **Examples** | five of them. `examples/filings-sample` is the shape this is built for |

Qanat makes no network calls of its own. The only ones are what your own sources make.

## Status

**Beta.** It does what this page says on my own work, and few other people have run it. If something
breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or say so on
[Discord](https://discord.gg/JUmwATScS8).

**You bring the data.** Nothing in here reads a company filing yet. There are generic connectors and
no SEC parser, so loading real accounts is your job, or point it at a feed you already have.

**It does not trade.** Qanat produces a list. It does not place an order, and no agent it serves
ever will.

**What it does not do yet.** No industry grouping, so a ranking is against whatever list of companies
you give it. No backfills. No incremental updates.

**One honest limit.** The two dates stop a *step* from seeing the future. They do not stop *you*, or
your agent, from reading ahead while deciding what to try. The data cannot cheat. A person still can.

**Being rebuilt right now.** This page describes where Qanat is going. The package still carries
pieces of what it used to be, a backtesting engine, including five fixed layer names and a results
layer that is becoming a plain query.

**What this is not.** Airflow, Dagster and Prefect run general pipelines and run them across many
machines. Use those for that.

Issues and pull requests welcome. See
**[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md)**.

## License

MIT. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
