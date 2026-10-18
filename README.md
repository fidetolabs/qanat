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

## What it does

You load company data. You write a rule. Qanat gives you the companies that pass it.

```
your data  ──▶  Qanat  ──▶  "these 12 companies passed"
                  │
            your agent asks
```

You write the rule. Qanat does not pick your numbers and never tells you what to buy.

## Why not just use a screener

Every screener shows you what passes **today**.

Qanat shows you what passed on **any day**. Last year. Five years ago.

That is harder than it sounds, and here is the whole reason:

> A company's results come out a while after the period they cover. Before that day, nobody could
> read them.

So if you ask "what passed in 2022?" and the tool uses numbers published in 2023, it is reading the
future. Your list looks great and you could never have picked it.

Qanat keeps **two dates** on every number: the period it covers, and the day it became public. Ask
about 2022 and you see only what was public in 2022.

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

## How it fits together

Three pieces, and only the first one is stored.

**1. Your data, in layers you name.** A source lands data as it arrived. Steps you write clean it up
and type it. You choose the layers and what to call them; Qanat ships no fixed set.

**2. Your metrics and rules, saved.** A metric is a formula you wrote. A rule is a threshold on it.
Qanat keeps both so you can reuse them, and keeps versions so an old answer still means something.

**3. Screens, computed when you ask.** Nothing is stored. Qanat reads the facts, works out your
metrics, applies your rule, and hands back the list. Ask again for a different date and it works it
out again.

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
