# Changelog

## 0.6.1 — 2026-10-24

### The size claim in 0.6.0 was wrong

It said the example corpus is 6.7 MB as parquet against **37 MB** in a DuckDB
file. The 37 MB was that file as it sat on disk after the example had been
rebuilt many times: about half of it was space freed by earlier writes that
the file had not given back. Comparing a fresh file to a fresh file, it is
**7 MB against 18 MB**.

Still worth having, and still mostly one table -- `normalized.spans` holds the
filing text and goes 12.6 MB to 5.4 MB, because prose repeats itself and
compresses hard. But two and a half times is the honest figure, not five and a
half, and the README said the wrong one on PyPI.

Documentation only. No code changed between 0.6.0 and 0.6.1.


## 0.6.0 — 2026-10-24

### The store is parquet files read by DuckDB

`store: ./data` — a directory — is the default for a new project. A `.duckdb`
path and a `postgresql://` DSN both still work and are unchanged.

**The reason is the second person.** A DuckDB file takes an exclusive lock, so
while one person is ingesting, a teammate opening the same project is refused
outright. Not slowed — refused. That limit is written down in three places in
this repo as a *hosting* limit, and it is not: it is a limit on the second
person, and a team sharing a corpus meets it on the first day. Open source is
ordinarily used by teams.

With parquet the writer builds a new file and renames it into place. A rename
within one directory is atomic, so a reader globbing for `*.parquet` sees the
old file or the new one and never a half-written one, and a reader already
part-way through a scan keeps the file it opened. Nobody waits.

Measured on `examples/sec-10k`, while a writer was landing 600,000 rows: the
reader answered in 1–3 ms. On a DuckDB file the same reader gets `StoreBusy`.
Both cases are tests.

### It is also smaller, by about two and a half times

The forty filings in `examples/sec-10k` are **7 MB as parquet against 18 MB in
a freshly built DuckDB file**, same rows.

Nearly all of the difference is one table. `normalized.spans` holds the filing
text -- 904 sections of English, some over 100 KB -- and goes 12.6 MB to 5.4 MB.
Prose repeats itself, so it compresses hard. `facts` is 4.7 MB to 1.4 MB and
the small tables barely move, because an empty DuckDB file is half a megabyte
before anything is in it.

The two formats are packed for different jobs. DuckDB keeps its pieces small
enough to reach one row, which is what updating and deleting need. Parquet
keeps them big, which compresses better and suits a table that is written once
and scanned. A filing is never edited, so nothing is given up here.

**The figure first published for this was 37 MB, which was wrong.** That was
the DuckDB file as it actually sat on disk after the example had been rebuilt
many times, and about half of it was space freed by earlier writes that the
file had not given back. The honest comparison is against a fresh file.

### What the tables look like

One file per table, under `<store>/tables/`:

```
data/
  tables/
    raw__documents.parquet
    normalized__facts.parquet
  meta.duckdb          the run log, opened only when something asks for it
```

**The metadata is the half that still wants a database**, because runs, events
and applied state are updated row at a time. It stays DuckDB, in its own file,
and is attached lazily — a caller that only reads data never opens it. That is
what keeps a teammate's query working while an ingest holds the log.

The cost is named: a pure reader gets no last-written timestamp on
`table_info`, because fetching it would mean taking the one lock the design
exists to avoid.

### Equivalence is the claim, so CI runs the suite twice

`QANAT_TEST_STORE=parquet uv run pytest` runs the whole suite against the new
backend. Same tests, same assertions. CI runs both on every push, because a
storage kind that passes its own tests and quietly fails the real ones is worse
than not having one.

### Fixed on the way

**Opening the run log lifted the as-of views.** Attaching the metadata file
reset `search_path`, which took the point-in-time schema off the front of it.
Every step then read the live table and the lookahead guard caught it as a
leak — the right failure for the wrong reason. One method now builds that
string and three callers use it.

**Retention reached into `store.con` with raw SQL.** It is `store.prune_rows()`
now, which a parquet store answers by rewriting the rows that stay.

### Breaking

Nothing. A project with `store: ./data/qanat.duckdb` behaves exactly as it did.
`qanat init` writes `store: ./data` for new projects only.

## 0.5.0 — 2026-10-19

### Real filings, and nothing invented left in the package

Every bundled example was made up, and three of the five did not load at all:
`equity`, `filings-sample` and `scheduled-ingest` still carried `split:`,
`benchmark:`, `bar:` and a double producer, which 0.4.0 made validation errors.
Nothing held them against `qanat check`, so nobody found out.

All five are gone, along with the `synthetic` connector that fed them. In their
place is one example built from documents the SEC published.

**`examples/sec-10k`** — eight companies, five years each, forty 10-K filings
downloaded from EDGAR on the first run. Apple, Microsoft, Alphabet, Meta,
NVIDIA, Walmart, Target and Costco: two groups that are each internally
comparable and not comparable with each other, which is what a relative screen
needs and what an honest one admits. 43,104 tagged numbers and 904 document
sections.

Their fiscal years end in January, February, June, August, September and
December, and the gap between period end and filing runs from 24 to 47 days. A
pipeline that only ever sees 31 December year-ends never has to get the clock
right. This one does.

### Three new pieces

**`sec` connector.** Resolves tickers, follows the paginated submissions pages
that a frequent filer's older 10-Ks hide behind, fetches each primary document
and stores it gzipped. The row it lands uses the platform's names — `id`,
`entity`, `doc_type`, `published_at`, `period_end`, `source` — with the SEC's
own keys in one `source_keys` field, so the core schema never learns what a CIK
is. Needs a contact in the User-Agent, which the SEC requires and which this
refuses to guess.

**`qanat.xbrl`.** Reads inline XBRL: resolves every context into a period and a
dimension set, resolves units including ratios, and applies `scale`, `sign` and
the transform a `format` names. Those three are the ones that matter — get any
wrong and the number is off by an order of magnitude or by its sign, and stays
plausible. Dimensions are kept as sorted JSON, because segment revenue and
total revenue are otherwise the same row. No new dependency: inline XBRL is
XHTML and the standard library parses it.

**`qanat.sections`.** Splits a filing into its Items and records how each
boundary was decided. Most matches for "Item 1A" in a 10-K are the contents
page or a cross-reference, not the heading, so the title is the signal and the
`rule` travels with the span.

### `qanat init` scaffolds against EDGAR

The starter pulled from the fake market and ran offline. It now pulls two
companies and two years of real 10-Ks, which needs a network and
`SEC_USER_AGENT` but no account and no API key. `--demo` runs the pipeline
instead of wiring a basket and pricing it.

The test suite grew a `conftest.py` that writes its own CSV-backed project, so
tests stay offline. A fixture may be invented where an example may not.

### Fixed

The package description 0.4.1 corrected in `pyproject.toml` was still in two
other places: `qanat --help` and `qanat/__init__.py` both announced an MCP
server that "tests each one inside its own industry", which is the claim 0.4.0
removed.

`examples/` is now held against `qanat check` by a test, which is how three
broken examples shipped.

### Screen is the only word, and it takes one date or a window

`qanat backtest` is gone, and so is the engine behind it. What replaces it is
`qanat screen` and a `screen` MCP tool, which answer the question the product
is named after.

```bash
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --as-of 2025-12-31
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --from 2021-12-31 --to 2025-12-31
```

A window of one is a single date, so the rows come back the same shape either
way and a caller never branches on which form it asked for.

**Three outputs, and nothing else.** The list of who passed. Each company's
own forward return, from its filing to its next one. And the count of how many
could be evaluated at all, because a screen that quietly dropped half the
market has said something false about who passed.

Nothing is added up. No average across years, no comparison against the
companies that failed, no equity curve. The returns run between each company's
own dates, because Costco files in October and Walmart in March and there is
no shared calendar to price them on.

**`replay` is not a word any more, in the engine either.** It meant running
the graph once per as-of date, which is what screening over a window is. One
of the two had to go and the product is named after the other. 124 occurrences
across the package, including the marker written into the store — which is
read under both spellings so a store from 0.4.x still recovers correctly.

### Nothing simulates holding a position

Gone: turnover, fees, slippage, net, the equity curve, the hit rate, decay
blending, basket combining, the `pnl` stage and the `backtest:` block. Pricing
a selection as a portfolio needs a decision about how much of each company to
hold, and that decision is the user's and is made outside this package.

This is the second half of a cut 0.4.0 started. 0.4.0 removed the `weights`
MCP tool; the engine behind it stayed, and the README had to carry a paragraph
apologising for it.

### The layers are yours

`kind` stops being an enum of five. Only `raw` means anything to the platform:
a landed stage, written by sources and never by a step. Everything else is a
name you chose. `weights_stage`, `pnl_stage`, `weights_steps`, `weights_name`
and `weights_step` are gone from the model.

`docs/contract.md` is rewritten around the four properties that survive. The
cost is real and it is stated there: `check` can no longer catch a cleaning
layer placed after something computed, so a pipeline can be valid and still
compute nonsense.

### Metrics, and the line between a formula and a threshold

A `metrics:` block defines formulas with names and versions. A metric is
arithmetic — `pe = price / diluted EPS` — so it is shared and the same for
everybody. A threshold is a judgement, so it arrives with the call and never
lives in the project. A clause pairs the two.

Bump `version` rather than editing a formula in place: a saved answer points
at a version, so an edit silently changes what an old answer meant.

### The reading surface: five tools

`list_sections`, `read_section`, `search_documents`, `read_facts` and
`check_number`. Each has one rule behind it.

`read_section` pages and never returns a whole Item — Item 1A runs past 100 KB
in a large filer, and a tool that hands that over has spent the context the
agent needed to think with. `list_sections` reports each length first, and
which rule decided each boundary. `search_documents` returns locations and
never bodies. `check_number` puts a number you read in prose next to the
tagged numbers near it and **refuses to say which is right**, because a gap
means either the prose was misread or the two are different measures of the
same subject, and the tool can tell neither from the other.

Which tables these read is a `corpus:` block, because the platform does not
name layers.

### Prices, from Yahoo, with the licence recorded

A `yfinance` connector behind the `prices` extra. Both closes are kept and
which is which is a column: the printed close is what was true that day, and
the adjusted one is restated for every later split.

**It is not a source anything may be sold from.** Yahoo's terms do not grant
redistribution, so every row carries `source: yfinance` and that column is
what a later cleanup acts on. A store that mixes these in with licensed rows
and does not record which is which cannot be separated afterwards.

### The MCP surface

23 tools at `full`, 15 at `research`, 12 at `data`. Was 21 / 13 / 6.

Nothing in `research` writes anything at all now. A screen is a query, and
there is nowhere in Qanat a per-user result could correctly live, because the
store cannot tell two callers apart.

### Breaking

`qanat backtest`, `qanat backtests` and `qanat report` are gone; use `qanat screen`.
The `backtest`, `backtest_conditions`, `list_backtests`, `report` and `period` MCP tools go with them.
`backtest:` in a project file is rejected, as are `rebalance:` and `decay:` on a step.
`connector: synthetic` is gone; use `csv` with a file you wrote, or `sec`.
`qanat.sort` and `qanat.backtest` are gone, and so is `qanat init --demo`'s basket wiring.
`scaffold.add_demo_steps`, `DEMO_INDICATOR` and `DEMO_SORT` are gone.
Every bundled example is gone, replaced by `examples/sec-10k`.

### Not in this release

The metric map. `pe` and `net_margin` in the example are a worked example of
the problem, not a solution to it: filers do not agree on a tag, and NVIDIA
uses `Revenues` where Apple uses
`RevenueFromContractWithCustomerExcludingAssessedTax`. Taking one and not the
other produced a 271% net margin before it was caught. Mapping 50 to 100 plain
names onto the tags filers really use is the large piece of work left on the
fundamental side.

A price source anything may be sold from. `yfinance` unblocks the build and is
not that.

## 0.4.1 — 2026-10-18

### The package description said the thing 0.4.0 removed

PyPI and every package index read the one-liner out of `pyproject.toml`, and that line still said
*"an MCP server that turns filings and prices into valuation indicators, and tests each one inside
its own industry."* Testing an indicator inside its industry is exactly what 0.4.0 took out, so the
release shipped with a tagline contradicting it.

It now reads: **a data platform for investment research. It ingests financial data sources and
computes point-in-time financial metrics for valuation and screening. Delivered as an MCP server.**

Keywords follow: `backtesting`, `indicators` and `feature-engineering` out; `investment-research`,
`stock-screener`, `point-in-time`, `financial-data` and `data-platform` in.

Metadata only. No code changed between 0.4.0 and 0.4.1.


## 0.4.0 — 2026-10-18

### Qanat is a data platform for investment research, not a backtesting engine

> A data platform for investment research. It ingests financial data sources and computes
> point-in-time financial metrics for valuation and screening.

A data platform does four things: ingest, save, transform, serve. Plus an orchestrator that runs
those four and checks each one did what it should. This release removes everything in the package
that was not one of them. **2,120 lines out, 104 in.**

### Nothing in it judges a result any more

Gone: the significance bar (`bar:`, `read_bar`, `set_bar`), the trial ledger (`record_trial`,
`list_trials`), the indicator IC block (`--indicator`, `indicators:`, `ic`, `spread`, `autocorr`,
`ic_mean`, `ic_t`, `ic_hit`), run comparison (`qanat compare`, `compare`), `vol`, `sharpe` and the
t-statistic.

The reason is arithmetic rather than taste. An annual rebalance's sample size is the number of
years, not the number of companies, because every company in one year shares that year. At fourteen
annual rebalances, which is every year of XBRL filings there is, a significance test needs an IC of
0.069 and a real value indicator is 0.02 to 0.06. **The test fails on a true effect at any history
length anyone can have**, so printing it was worse than printing nothing.

What replaces it: a replay reports one row per rebalance date, and `periods` sits next to the
number. A figure from four rebalances and a figure from four hundred are not the same claim.

### Nothing in it simulates holding a position

Gone: the `weights` MCP tool, `benchmark:` and `benchmark_returns`, `benchmark_net`, `excess_net`,
and the in-sample / out-of-sample split (`split:`, `segment`).

A benchmark comparison and an excess return are statements about a portfolio, and a portfolio needs
a decision about how much of each company to hold. That decision is the user's and is made outside
this package.

### Live scoring is gone

Gone: `live:`, `live_steps:`, `live_from:`, the frontier that separated live from out-of-sample,
`live_window`, and the scheduler's forward-scoring pass. Live left the scope in 2026-09-04 and the
code had not caught up.

### The unattended pass is gone

Gone: `research.py` and `headless.py`, the `research:` and `agent:` blocks, and the scheduler hooks
that fired them. That was a loop driving an installed agent CLI overnight to try to break a strategy
and record every attempt, which is factor validation with a robot doing it.

**This one is a real loss and worth naming.** It was the only part of the package that worked while
nobody was watching, and the trial counter was the only defence against trying fifty indicators and
reporting the best. Neither is replaced. The product no longer makes the claim they protected.

### What stayed, and why

`scheduler.py` and `retention.py` were both on the cut list and both came back. The scheduler is the
orchestrator: ingestion is scheduled too, not only transformation, and a platform that only moves
when a person asks is not a platform. Row retention is part of saving.

The point-in-time replay stayed and is the most valuable code here. Each date reads only the rows
that were public then.

### The MCP surface is smaller

21 tools at `full`, 13 at `research`, 6 at `data`. Was 27 / 18 / 6. Every tool definition is sent on
every request, so this is a real cost saving per call as well as a shorter list for a model to pick
from.

### Breaking

Any project with `bar:`, `benchmark:`, `split:`, `indicators:`, `groups:`, `live:`, `live_steps:`,
`live_from:`, `research:` or `agent:` in its `qanat.yaml` will fail validation. Delete those keys.
Callers of the removed MCP tools or `qanat compare` will get an unknown-tool error.

### Not in this release

Three decisions are made and not built: user-named layers instead of the five fixed kinds, versioned
metric definitions with their values kept, and screening as one call rather than a step you write.
The README says which parts of it are the direction rather than the present.


## 0.3.2 — 2026-10-12

### An indicator can be scored on its own, not only through a basket

`qanat backtest --indicator features.momentum:momentum`, or `indicators:` in the `backtest:`
block, scores a column alongside the basket. Repeat the flag for several.

| | |
| --- | --- |
| `ic` | rank correlation between the column and the return that followed, per as-of date |
| `spread` | the top group's mean return minus the bottom group's, over `groups:` groups (default 5) |
| `autocorr` | rank correlation with the same column on the previous as-of date |

The summary carries `ic_mean`, `ic_t` and `ic_hit`, the share of dates the correlation was
positive. `qanat backtest` and `qanat report` print the block, the `backtest` MCP tool takes an
`indicators` array, and `report` returns both levels under `indicator_summary` and
`indicator_periods`.

**Nothing existing changes.** Two keys are added to a backtest result and two fields to
`BacktestResult`, named `indicator_periods` and `indicator_summary` the way `periods` and `totals`
already are. `_qanat_bt_periods` keeps its ten columns, `Period` keeps its ten fields, and a run
asked for no indicators returns an empty list for both.

Until now the only way to ask whether an indicator meant anything was to hold the top of it and
look at the money. That answers one cut of the ranking. A column can order the universe and still
lose in a basket of four names, and the reverse happens as often.

Both numbers are measured on the period's own two price days, so the fill rule does not change:
the return starts at the first price strictly after the as-of date. An indicator row only exists
for a period that was priced.

`ic` is `null` rather than `0.0` where fewer than three names could be compared, or where the
column or the returns hold one value. Where fewer than two names land in each end, no spread is
reported and the run says so once, naming the `groups:` value that would fit.

**One sweep is not one trial.** Scoring twenty columns is twenty attempts at finding something.
The engine does not record them for you: `record_trial` is still the caller's call, and
`bar: {rule: count}` is what divides the significance by how many there were.

New table `_qanat_bt_indicators`, one row per indicator per period, created on open like the rest
of the meta tables. Nothing existing changes shape.

### The description, and the page

`qanat --help`, the package summary and the README all said *an MCP server for quant factor
processing and backtesting*. They now say **an MCP server that turns filings and prices into
valuation indicators, and tests each one inside its own industry**. No code behaves differently.

The README was also a release behind: it offered four shelf strategies that 0.3.1 deleted, counted
31 tools where there are 27, and typed the `normalized` stage as `kind: features`, which `qanat
check` now refuses. `docs/agents.md` had the three scope counts wrong for the same reason.


## 0.3.1 — 2026-10-11

**Breaking, despite the patch number.** Four tools are deleted, two CLI flags are renamed,
one `qanat.yaml` field is renamed, and a database column is renamed on open. The upgrade
list is at the end of this section; read it before installing over a project you care about.

There are no deprecation aliases: registering old and new names doubles five tool
definitions, and every definition is sent on every request.

### `alpha` is deleted, and nothing replaced it

`docs/words.md` defined an alpha as *the step that writes a weights table*, which is
`step` plus a destination stage and no information of its own. A second word for one
thing is what the first line of that file forbids.

So the thing has no name, and the stage is what identifies it. `list_steps` gained a
`stage` filter, which is how you find what produces the weights tables now, and a `net`
flag that joins the replay history per step: `runs`, `last_run`, `last_net`, `best_net`,
and which step is wired. A step removed from the file keeps its history, because what it
earned still happened.

| Was | Is |
| --- | --- |
| `alpha_book` | `list_steps(stage: "weights", net: true)` |
| `list_alphas`, `read_alpha`, `use_alpha` | deleted with the shelf |
| `qanat alphas` | deleted |
| `--alpha` on `qanat backtest` | `--step` |
| `alpha` on the `backtest`, `list_trials` tools | `step` |
| `alpha` in the `bar:` block | `significance`, which is what it always meant |
| `live_alphas:` in `qanat.yaml` | `live_steps:` |
| `Project.alphas`, `.alpha()`, `.alpha_name()` | `.weights_steps`, `.weights_step()`, `.weights_name()` |
| the `alpha_` step-id prefix | dropped. The stage says it |

`list_steps` and `read_step` move from `full` to `research`. Reading a definition is not
authoring one, and the pair existed only because the shelf offered a research-scope view
of something else. **27 tools, down from 31.**

### The shelf is gone, and one sort replaced it

It held `momentum`, `reversal`, `low_vol` and `neutral_momentum`. Each computed one
number per symbol per date and then did the same three things: rank the universe, keep
the top few, weight them equally. A number per symbol per date is an **indicator**, and
it belongs in the features stage with every other computed column.

So there is no catalogue. `qanat init --demo` writes one indicator step and one sort over
it, and the variation lives in `options.column` rather than in a different script. That
is also the honest shape for what the engine does: testing whether an indicator meant
anything requires building a paper basket from it on each rebalance date, and the
procedure for doing that never changes.

### `normalized` is a stage kind

The scaffold always listed five stages and the enum had four, so the stage holding
point-in-time facts carried `kind: features` and the contract could not say where it
belonged. It can now: after `raw`, before anything computed, and `qanat check` enforces
both. Existing projects keep working, because a kind is a label — but a project written
before this release should change that one line.

### One database migration, and it is automatic

`_qanat_backtests.alpha` is renamed to `step` on open. Databases written before today are
renamed rather than re-created, so every backtest anyone already ran keeps its row.
Nothing else persisted changes, and no table is renamed.

The rename is **asked for rather than attempted**. A rename that fails because the column
is absent looks exactly like one that fails because the database is unwritable, and
swallowing the second to tolerate the first is how a migration goes quiet. So it reads
`information_schema` first. There is nothing to run: the first open of this version does it.

### Upgrading

| Was | Is |
| --- | --- |
| `alpha_book` | `list_steps(stage: "weights", net: true)` |
| `list_alphas`, `read_alpha`, `use_alpha` | deleted with the shelf |
| `qanat alphas` | deleted |
| `qanat backtest --alpha` | `--step` |
| `alpha` on the `backtest` and `list_trials` tools | `step` |
| `alpha:` in the `bar:` block | `significance:` |
| `live_alphas:` in `qanat.yaml` | `live_steps:` |
| `Project.alphas`, `.alpha()`, `.alpha_name()` | `.weights_steps`, `.weights_step()`, `.weights_name()` |
| the `alpha_` step-id prefix | drop it. The stage says it |
| `kind: features` on your `normalized` stage | `kind: normalized` |

180 tests, 6 skipped, and 13 audit batteries including the leakage one.

## 0.3.0 — 2026-09-30

### Qanat is an MCP server for quant factor processing and backtesting

Point it at any source that carries a timestamp. It processes that into factors, stores
them, replays the whole chain over history one date at a time with no lookahead, prices
what the portfolio held after fees and slippage, and serves all of it to your agent over
MCP.

That is what Qanat has always done. This is the release where the package says it and
says nothing else.

```bash
claude mcp add qanat -- qanat mcp --scope research
```

31 tools. Ask "what feeds the momentum strategy", "test this over 2015 to 2024 and tell
me what survived costs", "why did it lose money in March". Your agent reads the tables,
runs the replay, opens the period, and shows what was held.

### Three scopes, and each one is a promise

`--read-only` split the tools 23 and 10. That describes how the code is written and
promises nothing anybody could build against. It is replaced by three nested scopes:

    qanat mcp --scope data        6 tools   read the tables, including as-of
    qanat mcp --scope research   19 tools   adds replays, results, comparisons
    qanat mcp                    31 tools   adds authoring, ingest, scheduling

`data` is the door an institution connects at. `research` is the door a hosted service
connects at. Each is a published contract, so adding a tool to `data` changes what
somebody has already built against.

`scope` has no default on the tool decorator, so a tool cannot be added without naming
who may see it. And the split does real work: `set_bar` sits in `full` while
`record_trial` sits in `research`, so a caller can write down what it tried and cannot
lower the line those attempts are held to. A tool above your scope names its scope
instead of pretending not to exist.

### It no longer has to be on your machine

```bash
qanat mcp --http --port 8421 --scope research --token "$QANAT_MCP_TOKEN"
qanat serve     --port 8421 --scope research --token "$QANAT_MCP_TOKEN"   # and the scheduler
```

MCP's Streamable HTTP transport on `/mcp`, with sessions. The scope is fixed by the
command that starts the server, so nothing in a request can widen it. It binds to
127.0.0.1 and refuses any other address without a token.

`qanat serve` is that server with a clock: it opens the store, runs the scheduler, and
mounts the same endpoint, so one process holds the store and everything reaches the
project through it.

One process serves one project, because the store takes one writer. Two projects means
two processes, and many tenants means Postgres and a store each.

### What you install is the engine and the server

The console is gone, and so is the terminal app and the HTTP API behind them. Your agent
client is the screen.

That is about 14,000 lines out of the package, and it is the point rather than the cost.
A person who opened this repo used to see a chat window and a dashboard and decide what
Qanat was before reading a word. The answer to "agent? tool? engine?" was the code, not
the README. What installs now is the replay engine, the CLI that operates it, and the MCP
server, and nothing you have to look at.

`agent.py` is `headless.py`, because the filename was making the claim.

### The unattended pass got narrower and better fenced

It tries to break your best strategy overnight and records every attempt, and it used to
do that through a dozen curl endpoints with `--allowedTools Bash`. That is a wide door.
Asked once to reshape some ideas, the agent walked out of the project into Qanat's own
installed source and then into `~/.claude/projects`.

It now reaches the project over MCP at `--scope research`, with the tools that scope
offers and nothing besides. The scope is also what stops it editing the strategy it is
supposed to be attacking. That used to be a sentence in a prompt.

### Negative costs are refused everywhere, not in one place

A negative fee pays you to trade, so turnover becomes profit and the run lands in the
strategy book looking like a discovery: -9999 bps once put +1938% into it with nothing
marking it. That was refused by a `ge=0` on the console's request model, which meant it
held for exactly one caller. It is in the engine now.

### Upgrading

`--read-only` is gone: use `--scope data` to read tables, or `--scope research` to add
replays. `qanat serve` no longer opens a console and no longer serves `/api/*`; it serves
`/mcp`. `qanat tui` is gone. Everything else on the CLI is unchanged.

180 tests, and the release check that used to verify the wheel carried console assets now
verifies it carries none.

## 0.2.0 — 2026-09-21

### The conversation is the console

The Ask box was one line, repeated identically on four pages -- identical because it
had no relationship to any of them. A text field above an interface you drive
yourself is the wrong shape for a console whose agent can already reach every tool
it exposes.

The thread has a side now, and the space beside it stopped being a page and became
a response surface: ask about a table and the table opens, ask for a replay and the
equity curve is what you are looking at when the answer lands. The tab row is gone,
because it named the same four destinations the state spine already names.

Nothing new is reported to make that work. The agent talks to the console's own HTTP
API, so every tool call was already a request arriving at this server; a middleware
notes what was asked and which surface it is about. The page stamps its own fetches
so they stay out of it -- without that the console feeds itself, a repaint landing in
the trace that tells the console to repaint.

The reply streams. Server-sent events carry the text and the tool calls on one
connection, so their order is the order they happened in, and the client walks toward
the text a few characters a frame because a model emits a clause at a time.

### Four surfaces, and one of them was missing

**Data** profiles what you connected: fill, distinct count and the span each column
covers, computed in the database rather than by paging rows. The coverage bar is the
point -- it turns "57 rows" into "this reaches across a seventh of your history".

**Alpha** is the graph, and steps are wired on it by clicking the tables that feed
them. `from:` has always been a list and the scaffold's own `portfolio` alpha reads
three, but every editor offered one.

**Live** had an engine nobody could reach. It has a page: the forward return drawn
against the rate the out-of-sample half implied, the next pass date, and what it is
holding now.

### Fixed

- Live scoring never ran in a project with more than one alpha. The scheduler called
  `run_backtest` with no alpha, the refusal went to the event log, and the pass was
  retried every thirty seconds forever while the console said everything was fine.
  `live_alphas:` names the choice; a project that cannot say gets one error and stops.
- `live_from` was stamped before the run, and it writes once ever -- so a pass that
  then failed left the date defining out-of-sample pointing at a moment nothing was
  ever scored at. It is stamped after the pass lands.
- Periods that held nothing were counted as losses in the hit rate and as zeroes in
  the per-period average. `totals` now carries `held_periods` and `flat_periods`
  alongside both figures recomputed over the periods that held something.
- `POST /api/steps` passed its body straight to `Step`, which forbids extras, so
  `source` died in pydantic -- the console had never once created a step.
- Opening any alpha and pressing save wrote an empty options block over its
  `lookback` and `top_n`, because those fields were only drawn for a shelf rule.
- `save_project` was `yaml.safe_dump`, so the first edit anything made deleted every
  comment in `qanat.yaml`. Values are laid onto the tree parsed from the file now.
- The Ask agent wandered: asked to reshape some ideas it read qanat's own installed
  source and then `~/.claude/projects`. It runs with `Bash` alone and a brief that
  says where the project ends, and it no longer builds when asked not to.
- `use_alpha` refused on a project with no universe and said to edit `qanat.yaml` by
  hand, which an agent over MCP cannot do. `save_universe` is the tool that error
  now names.
- The four MCP write tools returned `{"files": [...]}` that never held files -- it
  was `rep.warnings` the whole time.
- The Results header on the backtest surface spilled over the strategies above it.
  Its rule ended in a comma and no block, so it ran on to the run log's rule and
  took `display:flex;flex:0 0 132px` -- which stacked the header onto three lines
  inside a bar 26px tall and un-hid the drag grip over the book.
- `qanat --version` and the banner `qanat serve` prints both said 0.1.3, because
  `__version__` was a literal that two releases bumped `pyproject.toml` without.
  It is read from the installed metadata now, so it cannot drift again.

### Added

- `profile_table` and `save_universe`, bringing the agent surface to 29 tools.
- `ruamel.yaml`, so an edit keeps the comments somebody wrote.

## 0.1.5 — 2026-09-15

### Ask it in plain English

The console has an input box on both pages, and it holds no key. It runs whichever
agent CLI is already installed and signed in -- Claude Code, Cursor -- headless, in
the project directory. Nothing to paste, nothing to bill, and the credential stays
where the person put it.

A DuckDB file takes one writer and the console is holding it, so the agent cannot
open a second `qanat mcp` in the same project. It is pointed at the console's own
HTTP API instead: the same service layer the MCP tools sit on, reached over the
loopback port already serving. One process, one writer, no lock.

What it did shows underneath as it happens -- the calls it made, then a `diff` of
what actually changed in the project, `weights.momentum · 4 → 5 rows`. The panel
floats rather than pushing the report down the page, folds away while it is still
working (the bar keeps a one-line ticker so nothing is lost), and `ask` becomes
`stop` while it runs. Stopping kills the process; whatever it already did stays done.

### Two pages, not one screen

Strategies answers "did my idea make money": the book as a table across the top,
then the report, equity curve first. Pipeline answers "how was this built": the
graph, the tables, the steps that wrote them. You only need the second when the
first gives you a number worth questioning, and the run log is on both because an
agent can be writing a step while you read a result.

The backtest form asks four questions -- alpha, from, to, rebalance -- with the
other eight under `costs and controls`. Nothing was removed; you are just not asked
about `embargo` before you have asked anything else.

### The console only answers to this machine

`POST /api/steps` writes a step script and `POST /api/jobs/<id>/run` runs it, so a
page that can reach the API can run code on the machine serving it. Binding to
loopback does not prevent that: a site you visit can resolve its own domain to
127.0.0.1 and call the console believing it is same-origin, and CORS never enters
into it. Every request is now checked against the name the browser asked for.
Anything but loopback is refused, and serving under a real hostname is a deliberate
setting: `QANAT_ALLOWED_HOSTS=qanat.example.com`.

### Who did it

Every event carries an actor -- `agent`, `you`, or `schedule` -- and the run log
shows it. A project that changes while you are reading something else should say
who changed it.

### Fixed

- The console's `api()` helper accepted an options argument and ignored it, so every
  caller that thought it was posting was quietly issuing a GET.
- The README led with the mechanism. It now says what the thing does first.

## 0.1.4 — 2026-09-13

### The console itself, in the terminal

`qanat tui` is the console without the browser. Three panes: the DAG on top, a chart
selector, and every alpha this project declares or has ever priced with its last run
beside it. Move with the arrow keys or `j`/`k`, Enter to open a result, `h`/`l` to
change chart between equity, drawdown, per period, turnover and holdings.

Enter on an alpha nobody has priced runs the replay and draws it as it happens. The
DAG fills in from the left once per as-of date, the curve gains a point per closed
rebalance, and the alpha's row counts up -- all off `progress.snapshot()`, the same
in-memory record `qanat serve` polls, read from the process doing the work. Only the
lineage the replay actually walks is drawn, so it is four boxes rather than seventeen.

No new dependency. Raw mode, the alternate screen, a repaint that sends only the rows
that moved, and the character grid `qanat graph` already draws on. Curves are braille,
which makes a 60x8 box a 120x32 plot; `--ascii` gives that up for terminals that need
it.

`q` will not quit out from under a running replay -- `Q` abandons it, and says that
the tables it rewrote need `qanat run`. A terminal that goes away ends the loop rather
than spinning on it.

### The console's picture, in the terminal

`qanat graph` draws the pipeline where you already are: a column per stage, a box per
table, the step that writes it on the arrow, and the four stage colours the console
uses. It reads `build_graph` — the console's own read model — so an arrow here is an
arrow there and a row count is the same `count(*)`.

Columns are layers rather than stages, because a features stage is allowed to chain
and an edge inside one column would have to leave it and come back. A stage owns as
many columns as its longest chain, and the rule across the top says which. An edge
that skips a column is given a row to pass through, and nothing else is placed on
that row — two lines sharing a row in a character grid are one line, and would claim
an edge that is not there.

`--color auto|always|never` (and `NO_COLOR`), `--ascii`, `--no-labels`, `--width`.
Piped, it keeps its full width and drops the colour; on a terminal too narrow for the
step names it says so rather than wrapping.

## 0.1.3 — 2026-09-10

The last nine open findings from the audit, and one regression the audit found in
its own previous fix. Nothing is open now; six findings remain partly fixed, and
[`audit/FINDINGS.md`](audit/FINDINGS.md) says what is left of each.

### The graph is the real graph now

**A `.sql` body could read a table the step never declared.** `ctx.read` has always
refused an undeclared table and said why, but naming it in SQL went straight round
that. The consequences were all quiet: the console drew an arrow that was not where
the data came from, `qanat check` warned that a table two steps read "is never read
by anything", and `plan().stale()` never marked the consumer — so it stayed one
generation behind for ever while reporting `ok`.

Both doors are shut. `qanat check` reads the `.sql` body and errors on a table that
is in the project but not in `from:`; `ctx.sql` does the same at run time. Comments
and string literals are ignored, so a table name inside a comment is still a comment.

### A job that will not finish

**A job had no time limit and no way to stop it**, so it held its worker until the
process ended — and four of those stopped the scheduler dead, with nothing but warn
events to say so.

A job may now set `timeout: 10m`, or a project may set `job_timeout:` for all of
them. When it expires the scheduler frees the worker and closes the run row as
`timeout` instead of leaving it saying `running` for ever. `DELETE /api/jobs/{id}/run`
does the same on demand, and `/api/graph` now reports `workers` and `busy` so
starvation is visible without reading the log.

The limit is stated rather than hidden: nothing in this process can end a running
Python thread. The job finishes on its own and its result is discarded. What is
fixed is the scheduler carrying on, and the console no longer showing the job as
running.

### Smaller things that were left

- **The lookahead guard is honest about its one gap.** It returns early when a table
  has no recognised time column — and such a table is not filtered by the as-of views
  either. The step now says so when it writes one, and the backtest report lists them
  under `no_clock`, instead of leaving the promise wider than the code.
- **A store outside the project directory is called out** by `qanat check`. It is a
  real choice, not an error, but it means copying the project no longer copies the
  work.
- **`payload: true` is documented for the shapes that need it.** The docstring
  offered it for parallel arrays, which land correctly without it, while the two
  shapes that genuinely need it went unmentioned. It now describes the `records:`
  array index and the single-object body as well.

### The console, for anyone not using a mouse

- **A folded panel no longer keeps its buttons in the tab order.** `overflow: hidden`
  clips pixels and removes nothing, so a closed rail kept every button, chip, pager
  and form field focusable — and with no focus ring you were walking through controls
  you could not see.
- **`edit` on an alpha card is a real button.** It was a `<span>` with a click handler
  *inside* the card's own `<button>`, so pressing Enter on the card always picked the
  alpha and never opened it — editing was mouse-only, with no keyboard path at all.
  The card and `edit` are siblings now, and `edit` appears on focus as well as hover.
- **A column header is a control.** Sorting was bound to `<th>`, which cannot take
  focus and carries no state; it is a button now, with `aria-sort` on the header.
- **A rebalance can be opened without a mouse.** Clicking a bar opens one period and
  dragging narrows the report, both on a plain `<div>` — so the drill panel told
  people to "click a bar above" when they had no way to click one. There is now one
  control per rebalance beside the chart.

### A regression this audit caught in its own fix

The 0.1.2 fix for live progress put its two helpers in the wrong one of
`backtests.js`'s two IIFEs. `tick` threw `ReferenceError: backoff is not defined` on
its first call, its `catch` threw the same error again, and nothing rescheduled — the
progress poller was dead from page load, which is exactly what that fix was for.

It was found the same way everything else here was: by opening the console in a
browser and watching what it did. The poller is now checked by counting the requests
it makes, not by reading the code that should make them.

## 0.1.2 — 2026-09-09

A chaos audit of the whole tool, and the fixes it produced. Seventy-three findings
are written up in [`audit/`](audit/) with the scripts that produced them; fifty-eight
are fixed here, and `tests/test_audit_fixes.py` keeps each one from coming back.

Nothing below changes how a project is written. The demo, the examples and every
existing `qanat.yaml` still run — but several of them now report things they used
to swallow, and a few now refuse what they used to accept.

### An alpha could see the future

The as-of views a replay reads through had three ways around them, and a cheating
alpha earned **1292% against an honest 3.40%** with no failure, warning or note.

- **`ctx.store.read()` ignored the clock.** Reads are pinned to the home schema so
  that `search_path` cannot move a *write*; applied to reads it also meant the store
  handed back the whole history mid-replay. Reads now go through the as-of view.
- **A schema-qualified `ctx.sql("… FROM main.raw__bars")` stepped around them.** A
  replay never re-polls a source, so the raw tables are the one place the full
  history survives — and naming the schema reached it. `ctx.sql` now refuses a
  schema-qualified reference during a replay and says why.
- **A timezone offset was thrown away.** `CAST(col AS TIMESTAMP)` kept the wall
  clock, so a `16:00-05:00` row was visible four hours before it happened and a
  `+09:00` row nine hours late. Against the default DuckDB store every as-of
  comparison now goes through TIMESTAMPTZ, and the connection is pinned to UTC.
  Against an attached Postgres the plain cast is kept: the column there already
  carries its own zone, and a richer expression is rewritten when DuckDB pushes the
  view down, which Postgres then refuses.
- **`fetched_at` from the REST connector landed as TIMESTAMPTZ**, so off UTC every
  landed row was hidden from a replay for the length of the offset — right on a UTC
  server, nine hours wrong in Seoul. It is naive UTC now.

A step is arbitrary Python, so it can still read a source file off disk itself and
reach data the views hide. Nothing in-process can prevent that; it is documented as
the boundary rather than papered over.

### Numbers that looked right and were not

- **A holding with no price earned zero and was still counted as held.** The price
  frame is a pivot, so a delisted name stayed in the held set as a column of nulls:
  a quarter of a book became silent cash while the report said it held four names,
  with no note. Prices are intersected on non-null values now.
- **A loop between two steps passed `qanat check`** and multiplied feature values
  ten-fold on every run, every step reporting `ok`. `validate()` detects it.
- **A failed step no longer leaves its consumers running.** They recomputed from
  yesterday, reported `ok`, and stamped themselves up to date.
- **A `${var}` with no option behind it** stayed in the SQL as a literal that matched
  nothing: 0 rows, status `ok`. It is an error at check time.
- **Two steps could write one weights table**, and the book counted one portfolio as
  two alphas with identical net. One table, one producer.
- **`decay` rescaled the book, not just its turnover** — a long/short book that flips
  came out at a third of the size the alpha asked for.
- **The digest ignored the data**, so `compare` said "any difference here is the
  engine" when the numbers in a source file had changed. It now covers row counts and
  the newest row per landed table.
- Weights that do not sum to 1, duplicate symbols, and half-failed runs now reach the
  report and the strategy book instead of only the event log.

### Landing data from the web

Tested against live public APIs — the ECB, Nager.Date, Open-Meteo, USGS, Coinbase,
the World Bank and a CSV on GitHub.

- **`005930` no longer becomes `5930`.** The csv connector ignored `options`
  entirely; it now takes `dtype`, `sep`, `encoding` and the rest, and rejects an
  option it does not know instead of silently dropping it.
- **An epoch clock works.** Epoch seconds, epoch milliseconds and a plain `year`
  column are read by magnitude rather than raising `Unimplemented type for cast`.
- **A reordered feed no longer writes into the wrong columns** — the insert matches
  by name, and a changed column set is an error naming what appeared and what went.
- **A column that is all-null in the first response lands as text**, so the day the
  field carries data the feed does not stop.
- **A float no longer rounds into a column typed from whole numbers** — the column
  widens, and says so.
- **A null in a key no longer defeats dedup** for ever.
- **One bad symbol no longer loses the whole batch**, and the ones that failed are
  named in the log.
- **A single-object body lands**, and `records:` takes an array index for a
  `[metadata, rows]` body.
- **An unset `${VAR}` names itself** instead of becoming an empty string.
- `mode: replace` clears the table when the feed answers with nothing.

### Crashes, concurrency and limits

- **A replay that never finished is noticed and repaired.** It leaves a marker; the
  store clears the stranded state on open, and `qanat run` / `qanat serve` rebuild
  the tables it left truncated.
- **One replay at a time, whoever asks.** The lock lives in `run_backtest`, so the
  scheduler's live pass and a console run can no longer overlap and give each other
  the wrong answer.
- **`qanat.yaml` is written atomically**, under one lock, keeping a `.bak`. Two in
  five concurrent reads used to get a torn file.
- **A rejected edit no longer poisons the console.** Edits are validated on a copy,
  so one bad form field no longer takes the editor down until restart.
- **`sys.exit()` or Ctrl-C in a step** no longer leaves the run row saying `running`
  for ever.
- **A replay is bounded** — `2020..2021` every `1s` is 31.6M passes, and it says so
  instead of building the list.
- **Retention has a one-hour floor** and warns when it is pointed at a raw stage.

### Input that should not be an instruction

- **A table name is validated.** It is interpolated into DDL, and a name carrying a
  quote was a SQL statement that `qanat check` called clean.
- **A step's `script:` must be inside the project**, and the stub is written only
  after the edit is accepted — a rejected request used to leave a file on disk.
- **A negative commission is refused** at the model, at the API and in the form. It
  had produced `+1938%` in the strategy book with nothing marking it.

### The console

- **The pipeline graph can be used from a keyboard.** It is a canvas, so clicking a
  table was mouse-only; there is now a focusable control per table beside it, and the
  page went from 15 tab stops to 51.
- **A visible focus ring**, and `prefers-reduced-motion` is honoured.
- **A hung server is noticed** — every fetch has a timeout, so the console no longer
  reads "connected" while showing stale numbers.
- **The "server is down" banner cannot be hidden** by folding the graph, and a modal
  whose only content is a warning no longer opens blank.
- **Live progress survives a failed request.** Both early returns skipped the only
  reschedule, so one 500 stopped it for the session.
- **Errors are readable and survive the repaint** — `{"detail": …}` is unwrapped, and
  messages go to one area outside the polled panels.
- **Escape closes the form**, not the panel behind it.
- **Delete asks first**, and every form field has a label.
- The backtest-list error handler no longer writes to an element that was removed.

## 0.1.1 — 2026-09-08

Two console fixes, both found by using it.

**The backtest run form could not be scrolled.** `#editor-body` carries the
flex/overflow rules that let the alpha editor scroll; `#runner-body` never got
the matching rule, so the form kept its full content height whatever the window.
The wheel did nothing and `run it` sat below the fold, out of reach on any
display under about 1030px.

**An alpha could be pointed at a table that holds no prices.** Every script on
the shelf reads one table with a symbol, a date and a price, but the console
offered every table in the project and `save_alpha` checked only that the table
existed. Wiring one to something like a market-wide regime series saved cleanly,
passed `check`, then died once per as-of date mid-replay on a bare
`KeyError: 'date'` naming neither the table nor the column. The save now refuses
it and says which column is missing and what the table actually holds.

## 0.1.0 — 2026-09-05

First packaged release. Published to PyPI as
[`qanat-fdtl`](https://pypi.org/project/qanat-fdtl/0.1.0/).

### Provenance note

The git history of this repository was rewritten and republished after 0.1.0
was uploaded to PyPI. Two consequences, neither of which affects the published
package:

1. **The attested commit no longer exists.** PyPI's build attestation for 0.1.0
   names commit `f878c9f8f018bfc99e96a02544cb8699f7f8e056`, which was destroyed
   by the rewrite. An automated provenance check against that hash will not
   resolve. The attestation itself is signed and immutable, so it cannot be
   corrected.

2. **The published artifacts were never rebuilt or replaced.** What is on PyPI
   is exactly what was uploaded on 2026-09-05.

The source tree is unchanged, and that is verifiable. The `v0.1.0` tag points at
a commit whose tree is byte-identical to the published sdist:

```bash
git checkout v0.1.0
pip download qanat-fdtl==0.1.0 --no-binary :all: --no-deps
tar xzf qanat_fdtl-0.1.0.tar.gz
diff -r qanat_fdtl-0.1.0 . -x .git -x PKG-INFO   # no differences
```

PyPI file digests for 0.1.0:

| file | sha256 |
| --- | --- |
| `qanat_fdtl-0.1.0.tar.gz` | `42cbcb9939f5f0486fb665c918a73700ae4b41956119ffabaeff41f9081e2325` |
| `qanat_fdtl-0.1.0-py3-none-any.whl` | `da87e5baff12423a13445d9b8b76150e9c0ccceca2a57cf6db3cbe44196c0dfa` |
