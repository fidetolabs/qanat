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

## Why FidetoLabs built this

You are thinking about buying a company. You go and read about it. There are
posts explaining why it is going up, a video from someone who sounds like they
know, charts, and a summary of last quarter that reads like a professional
wrote it.

Any of that now takes a minute to make and costs nothing. Some of it is wrong,
and you cannot tell which.

The filing is still on EDGAR. It still says what the company told its
regulator, and reading it takes as long as it always did.

**FidetoLabs cannot make lying expensive again. It can make checking cheap.**
Qanat is the first tool for that: whatever number it gives you, it can show
you the filing the number came out of and the day that filing was published.

**You write the logic and Qanat runs the calculator.** The formula, the
threshold and the list of companies are yours. It tells you what came out and
never tells you what to do about it.

## What Qanat is

Qanat reads company filings, computes the metrics you define, and answers **as
of any past date.**

A company publishes its results weeks or months after the period they cover, so
every number here carries two dates: that period, and the day it became
public. Ask about March 2022 and you get what was public in March 2022,
not what we know now. Every other stock screener screens today.

Your agent is the interface. There is no dashboard to learn and no query
language to pick up.

**Not investment advice.** What you do with the number is your decision and
your risk.

## Getting started

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
export SEC_USER_AGENT="your name you@example.com"
qanat init my-research --demo && cd my-research
claude mcp add qanat -- qanat mcp --scope research
```

The demo pulls a few real 10-K filings from EDGAR and reads the numbers out of
them, in under a minute. No API key and no account: the SEC only wants a
contact address. Any MCP client works, with `qanat mcp` as the command and the
project folder as its working directory.

## Using it

Ask your agent. It can find filings on EDGAR, pull the ones you choose, define
a metric as a formula, and screen on it as of a date or across a window.

> *"What data is in here, and where did it come from?"*
>
> *"Pull Apple and Microsoft's last five annual reports."*
>
> *"Define net margin, then show me who passed it in 2022 and what they did
> next."*

**`--scope` decides how much it can do.** Four levels, each including the one
before: `data` reads what is already there, `research` adds finding filings and
screening, `full` adds ingesting and running, `authoring` adds defining metrics
and changing the pipeline. Pick the smallest that does your job.

`full` is the default, and `authoring` sits above it so an agent screening
filings cannot rewire the pipeline by accident.

[`examples/sec-10k`](https://github.com/fidetolabs/qanat/tree/main/examples/sec-10k)
is a corpus of real filings to try it on.
[docs/dataflow.md](https://github.com/fidetolabs/qanat/blob/main/docs/dataflow.md)
walks the pipeline end to end,
[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)
is the tool surface, and
[docs/flows.md](https://github.com/fidetolabs/qanat/blob/main/docs/flows.md),
[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)
and
[docs/words.md](https://github.com/fidetolabs/qanat/blob/main/docs/words.md)
cover why each tool exists, the pipeline rules and the vocabulary.

## The limits

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues)
or say so on [Discord](https://discord.gg/JUmwATScS8).

**It cannot place an order,** and no agent it serves ever will.

**Nothing is added up.** No average across years, no equity curve. That needs a
decision about how much of each company to hold, and the decision is yours.

**The dates protect a transform, not you.** It cannot see past its date; you
and your agent can, while you are deciding what to try.

**Two sources:** SEC EDGAR for filings and Yahoo Finance for prices. Only the
10-K and the 10-Q are parsed; other forms are found and labelled, not read.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).
MIT licensed. Copyright (c) 2026 fidetolabs.
