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

It costs almost nothing to make a claim about a company look true. It still
costs real work to find out whether it is.

> **cost of checking a claim  >  cost of making a convincing claim**

Both sides used to be high, and that is why the market worked. Writing a
research note, building a track record, arguing in the replies was all work, and
**the work was the filter.** Now one side has fallen. A post, a chart, a
quarterly summary, a video of a person you trust: minutes and pennies each.
**Reading a filing to find out whether the number is real costs what it always
did.**

**It lands hardest on the person doing the work.** Someone who skims and buys on
a tip was always going to lose money. The new exposure is the opposite person:
someone who saves out of a salary and studies before buying. They are exposed
*because* they study, because studying means going to look for material and the
material is the thing that got cheap to manufacture. The careless investor is
reached by one post. The diligent one reads twenty, and twenty is the supply the
cheap side controls.

**FidetoLabs cannot make lying expensive again. It can make checking cheap.**
That is the company, and Qanat is the first tool of it. The name is the promise:
fidelity is not accuracy, it is how strictly a copy represents an original, so
it always takes an object. Fidelity *to the filing*.

**You write the logic and Qanat runs the calculator.** The formula, the
threshold and the list of companies are yours. Qanat reports what came out and
never presents it as a finding, because a product supplying both the number and
the judgement has made the judgement, whatever its disclaimer says.

## What Qanat is

Qanat reads company filings, computes the metrics you define, and answers **as
of any past date.** A company publishes its results weeks or months after the
period they cover, so every number carries two dates: the period it covers and
the day it became public. Ask about March 2022 and you get what was public in
March 2022, not what we know now. Every stock screener on the internet screens
today, and that one difference is the reason for everything else here.

Your agent is the interface. There is no dashboard to learn and no query
language to pick up.

**Not investment advice.** What you do with the number is your decision and your
risk.

## Getting started

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
export SEC_USER_AGENT="your name you@example.com"
qanat init my-research --demo && cd my-research
claude mcp add qanat -- qanat mcp --scope research
```

The demo pulls a few real 10-K filings from SEC EDGAR and reads the numbers out
of them, in under a minute. There is no API key and no account: the SEC asks
only for a contact address so it can see who is making the requests. Any MCP
client works, with `qanat mcp` as the command and the project folder as its
working directory.

## Using it

Ask your agent. It has tools for finding filings on EDGAR, pulling the ones you
choose, defining a metric as a formula, and screening on it as of a date or
across a window.

> *"What data is in here, and where did it come from?"*
>
> *"Pull Apple and Microsoft's last five annual reports."*
>
> *"Define net margin, then show me who passed it in 2022 and what they did next."*

**`--scope` decides how much it can do.** Four levels, each including the one
before: `data` reads what is already there, `research` adds finding filings and
screening, `full` adds ingesting and running, and `authoring` adds defining
metrics and rewiring the pipeline. Every tool description is sent to the model
on every request, so pick the smallest that does your job.

**The default is `full`, which is not the widest one, on purpose.** The pipeline
from a filing to a metric is the same in every project, so an agent screening
filings has no use for the tools that rewire it, and carrying them means it can
rewire them by accident.

[`examples/sec-10k`](https://github.com/fidetolabs/qanat/tree/main/examples/sec-10k)
is a corpus of real filings to try it on. Then
[docs/dataflow.md](https://github.com/fidetolabs/qanat/blob/main/docs/dataflow.md)
walks the pipeline end to end,
[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)
is the tool surface,
[docs/flows.md](https://github.com/fidetolabs/qanat/blob/main/docs/flows.md)
says why each tool exists, and
[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)
and
[docs/words.md](https://github.com/fidetolabs/qanat/blob/main/docs/words.md)
hold the pipeline rules and the vocabulary.

## The limits

**It is beta.** It works for my own use and few other people have run it. If
something breaks, open an [issue](https://github.com/fidetolabs/qanat/issues) or
say so on [Discord](https://discord.gg/JUmwATScS8).

**It cannot place an order,** and no agent it serves ever will. **Nothing is
added up** either: no average across years, no equity curve. Adding returns
together needs a decision about how much of each company to hold, and that
decision is yours.

**The dates protect a transform, not you.** A transform cannot see past its
date. You and your agent still can, while you are deciding what to try.

**Two sources**, SEC EDGAR for filings and Yahoo Finance for prices. Only the
10-K and the 10-Q are parsed; every other form is found and labelled, and a
refused one is told why rather than failing quietly.

**For general pipelines, use something else.** Airflow, Dagster and Prefect run
any DAG across many machines. Qanat runs one shape.

Pull requests welcome. See
[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md).
MIT licensed. Copyright (c) 2026 fidetolabs.
