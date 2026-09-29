<p align="center">
  <img src="https://raw.githubusercontent.com/fidetolabs/qanat/main/assets/hero.jpg" alt="A qanat cut open: shaft mouths and fields on the surface, the tunnel running beneath them, and a man walking it" width="560">
</p>

<h1 align="center">Qanat</h1>

<p align="center">
  <i>From raw data to portfolio weights, defined in plain English.</i>
</p>

<p align="center">
  <a href="https://github.com/fidetolabs/qanat/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-a2e65d?style=flat-square"></a>
  <a href="https://github.com/fidetolabs/qanat/blob/main/pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-a2e65d?style=flat-square"></a>
  <a href="#status"><img alt="Status" src="https://img.shields.io/badge/status-beta-e8c069?style=flat-square"></a>
  <a href="https://discord.gg/JUmwATScS8"><img alt="Discord members" src="https://img.shields.io/badge/dynamic/json?logo=discord&logoColor=white&label=Discord&query=%24.approximate_member_count&url=https%3A%2F%2Fdiscord.com%2Fapi%2Fv10%2Finvites%2FJUmwATScS8%3Fwith_counts%3Dtrue&color=5865F2&style=flat-square"></a>
  <a href="https://www.instagram.com/fidetolabs/"><img alt="Instagram followers" src="https://pulse.walls.sh/badge?url=https://www.instagram.com/fidetolabs/&label=Instagram&color=E4405F"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="#use-it-with-an-agent">Agents</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#backtest">Backtest</a> ·
  <a href="#data-sources">Data sources</a> ·
  <a href="#status">Status</a>
</p>

Qanat is an agent-first backtesting engine that turns your raw data into portfolio weights
through a pipeline of steps you define.

## The idea

In Qanat, you build a strategy as a pipeline of tables.

It starts with your raw data: prices, news, or anything else you track. From there, you define
each step of the pipeline. A step reads one or more tables and writes a new one, so you can clean
the data, calculate metrics, and score your symbols. The final step outputs your portfolio
weights, detailing exactly what to hold and how much.

Qanat replays this pipeline across historical data, one date at a time. It prices the portfolio's
holdings, accounts for fees, and outputs a complete PnL table.

Nothing is hidden. Every table is visible on your screen, with its row count and the logic that
created it. If a number looks off, you open that table and inspect the data.

As your project grows, you can plug in new data sources and build new steps on top of them.

You don't have to write the code yourself. Describe what you want in plain English, and the agent
will build the step, run it, and show you the resulting table.

Qanat is built for retail traders who rebalance daily or weekly. You can't out-race an
institutional hedge fund on speed, and with a longer horizon, you don't need to.

<p align="center">
  <img src="https://raw.githubusercontent.com/fidetolabs/qanat/main/assets/console.gif" width="720"
       alt="The Qanat console. The session runs down the left -- what was asked, what the agent did. Beside it the surface follows along: connected data, the pipeline graph, a replay and its equity curve.">
</p>

<p align="center">
  <sub>One session, and the surfaces it moved through. Each strategy writes its own <code>weights</code>
  table, and each has a <code>pnl</code> table beside it holding what it earned. The session runs
  down the left; the surface follows what the agent is doing.</sub>
</p>

## Quick start

You need Python 3.10 or newer.

```bash
uv tool install qanat-fdtl                 # or: pip install qanat-fdtl
qanat init my-alpha --demo && cd my-alpha
qanat serve
```

The console opens on **http://127.0.0.1:8420**.

`--demo` builds four working strategies, runs the pipeline, and prices each one, so the console
opens with real numbers in it. It takes about fifteen seconds. The data is synthetic, so this
works with no API key and no network. Leave `--demo` off for an empty project.

Qanat makes no network calls on its own. The only outbound requests are the ones your data sources
make.

<details>
<summary>Run it in Docker instead</summary>

Needs nothing but Docker, and brings its own Postgres:

```bash
git clone https://github.com/fidetolabs/qanat.git && cd qanat
docker compose up --build
```

Postgres is on **localhost:5433**, not 5432, because 5432 is often taken already. User, password
and database are all `qanat`. Bind a directory to `/project` to use your own project instead of
the demo.

If a port is already in use, set the host ports yourself:

```bash
POSTGRES_HOST_PORT=5434 QANAT_HOST_PORT=8421 docker compose up --build
```

To start over, `docker compose down -v && docker compose up --build`.
</details>

## Use it with an agent

This is the main way to work with Qanat. You describe what you want, and the agent writes the
step, runs it, and shows you the table it produced.

Add it to any MCP client:

```json
{ "mcpServers": { "qanat": { "command": "qanat", "args": ["mcp"], "cwd": "/path/to/my-alpha" } } }
```

For Claude Code, `claude mcp add qanat -- qanat mcp`.

`--scope` decides which tools the agent is offered. Three of them, nested:

| | | |
| --- | --- | --- |
| `--scope data` | 6 tools | read the tables, including as-of |
| `--scope research` | 19 tools | adds replays, their results, and the trial ledger |
| *(default)* | 33 tools | adds authoring, ingest and scheduling |

Every tool definition is sent on every request, so a narrower scope is cheaper and leaves the
agent with a shorter list to pick from. [docs/agents.md](docs/agents.md#--scope) has the whole
table.

Things you can ask for:

- **"What is in this project, and what feeds the momentum strategy?"** The agent traces the table
  back to its sources and answers with data, not a guess.
- **"Add a momentum strategy and backtest it."** Before running anything, it comes back with what
  your data can actually cover and asks you to choose the window, the rebalance, and the costs.
- **"Which of my strategies actually works?"** It lists every one with what it earned, so it
  compares instead of speculating.
- **"Why did it lose money in March?"** It opens that period and shows what was held, what each
  name returned, and what was traded to get there.
- **"Does my news table line up with my prices?"** It profiles both in the database and answers in
  spans, not row counts -- which is how you find out a strategy across the two would hold nothing
  for eleven months before it earns a cent.

The console and the agent are two views of the same project, so they can never disagree about its
state -- and in the console they are one view. The agent talks to the console's own API, so what it
reads and changes appears in the thread as it happens and the panel beside it follows along: ask
about a table and the table opens, ask for a replay and the equity curve is what you are looking at
when the answer lands.

Which agent answers is a setting, not an accident of `PATH`:

```yaml
agent:
  cli: claude       # empty means the first one found
  timeout: 180s     # one question's limit; a research pass wants more
```

The full tool list is in
**[docs/agents.md](https://github.com/fidetolabs/qanat/blob/main/docs/agents.md)**, and what the
console does with it is in
**[docs/console.md](https://github.com/fidetolabs/qanat/blob/main/docs/console.md)**.

## How it works

Everything lives in one `qanat.yaml`. Nothing hides in application code.

```yaml
project: equity
store: ./data/qanat.duckdb

universes:                         # which symbols a portfolio may hold
  - id: sp500
    symbols: ./universes/sp500.csv

stages:                            # order here is order in the pipeline
  - { id: raw,        kind: raw }
  - { id: normalized, kind: features }
  - { id: features,   kind: features }
  - { id: weights,    kind: weights }
  - { id: pnl,        kind: pnl }

sources:                           # where data comes from
  - id: prices
    to: [raw.daily_prices]
    connector: rest
    options:
      url: https://api.example.com/v1/bars
      headers: { Authorization: "Bearer ${PRICE_API_KEY}" }

steps:                             # each one reads tables and writes tables
  - id: momentum
    from: [normalized.prices]
    to:   [features.momentum]
    script: steps/momentum.py
    options: { lookback: 20 }

  - id: alpha_momentum             # the step that writes weights is the strategy
    from: [features.momentum, features.risk]
    to:   [weights.momentum]
    script: steps/alpha_momentum.py
    universe: sp500
    rebalance: 20d

backtest:                          # what prices the portfolio, and what it costs
  prices: normalized.prices
  fee_bps: 5
  slippage_bps: 10
  live: false                      # true, and `qanat serve` keeps scoring it forward
  live_alphas: []                  # which one to price: not ours to guess once you have two
```

A step is a `.sql` file, or a `.py` file with a `run(ctx)` function:

```python
def run(ctx):
    bars = ctx.read("normalized.prices")      # only tables the step declared in `from`
    held = ctx.universe()                     # the symbols it may hold
    return df
```

`ctx.read()` refuses any table the step did not list, so a missing dependency is an error instead
of a wrong number.

### The five stages

Each stage holds tables, and data only moves forward through them.

| stage | what it holds |
| --- | --- |
| `raw` | data exactly as it arrived. Never edited |
| `normalized` | typed, deduplicated, one key set |
| `features` | anything you measure or calculate |
| `weights` | one table per strategy. What to hold, and how much |
| `pnl` | what each strategy earned. Written by `qanat backtest` |

`qanat check` enforces the rules that keep this honest, and refuses to run a project that breaks
one. They are written out in
**[docs/contract.md](https://github.com/fidetolabs/qanat/blob/main/docs/contract.md)**.

### The commands you need

| command | |
| --- | --- |
| `qanat init` | create a project |
| `qanat run` | run the pipeline once |
| `qanat serve` | scheduler and console |
| `qanat backtest` | replay over history and price what it held |
| `qanat report` | one backtest, period by period |

`qanat --help` lists the rest. `qanat tui` gives you the console in the terminal if you prefer to
stay there.

A source or a step can also run on a clock, or run whenever its input changes. Set `schedule:` or
`when:` on it, or fill it in from the console.

## Backtest

A backtest replays the pipeline over a period that already happened and prices what it held.

```bash
qanat backtest --from 2026-01-05 --to 2026-06-01 --rebalance 10d
```

```
    gross            +25.150%
    fees              -0.636%
    slippage          -1.272%
    ------------------------------
    net              +23.242%

    per period        +1.660%  over 14 periods
    hit rate           64.3%
```

Before each pass, every table is filtered down to the rows that existed at that moment. A step
reads the past without knowing it is being replayed, so a step that forgot to filter still cannot
see the future.

**Net is the headline, not gross.** Fees and slippage are charged on turnover, which is how much
had to be traded to reach the new portfolio. Trading more often can turn a winning strategy into a
losing one. Same strategy, same window, only the rebalance changed:

```
  --rebalance 10d     turnover   10.8     net  +23.2%
  --rebalance 1d      turnover  128.5     net  -27.1%
```

`--decay N` works from the other side. It holds a blend of the last N portfolios, so the strategy
stops paying fees for noise:

```
                    turnover      net
  --decay off          45.00    -5.05%
  --decay 4            25.32    -2.88%
```

**Net alone cannot be ranked.** Two strategies that earned the same amount are not the same
strategy if one of them halved on the way, and a long-only rule in a rising market is not skill.
So the report prints what the return cost in risk, and what it beat:

```
    net              +12.309%  compounded
    benchmark         +3.002%  doing nothing instead
    excess            +9.307%  what the rule added
    ...
    sharpe              0.68   per unit of its own vol, annualised at 73/yr
    max drawdown     -11.288%  worst fall from a high
```

Set `benchmark:` to a symbol in your price table, or to one of two floors that hold a list in equal
parts — `equal_weight` takes everything the price table carries, and `universe_equal_weight` takes
the run's universe on each date. The first asks whether the rule beat the market. The second asks
whether it beat *the pool it narrowed to*, which is the harder question and the one a rule that only
ranks inside a sector is actually answerable to. Without a benchmark the lines are absent rather
than zero: a project that has not measured this should not be able to read a number as though it
had.

**In sample and out of sample are reported separately.** `--split <date>` cuts the run in two. The
lookback, the rebalance and the decay were all chosen by someone who could see the first half, so
that number partly measures the choosing:

```
    in sample        -22.055%  19 periods,  -1.161% each
    out of sample     -2.843%  14 periods,  -0.203% each
    split at 2026-03-01 — out of sample is the line to believe
```

Several strategies can be priced together as one book with
`--alpha alpha_momentum,alpha_low_vol`. Each keeps its own weights table, and the result lands in
one PnL table.

More in **[docs/backtest.md](https://github.com/fidetolabs/qanat/blob/main/docs/backtest.md)**.

## How many did you try?

A Sharpe of 2.2 from one attempt is interesting. The same figure picked out of fifty is what
noise looks like — and nothing inside a backtest can tell you which one you are holding.

So every replay is a row in a ledger, and the count travels with the number:

```
    net 8.49%  ·  81 rebalances  ·  hit rate 47%  ·  1 of 8 trials
```

A trial is a **distinct question**, not a run. Re-running the same configuration gives the same
answer, so it is one trial; changing the lookback, the window, the universe, the rebalance or the
split is a new one. The digest already draws that line, so it is the key.

Record what an attempt was *for* with `POST /api/trials` — or the `record_trial` tool — giving it
a one-line hypothesis and the run it varied. Do it for the ones that failed especially: those are
the count that makes a surviving number mean anything, and they are the one thing nobody writes
down.

### The bar

`backtest.bar` decides how high a result has to be, given how many were tried:

```yaml
backtest:
  bar:
    rule: count      # none · floor · count
    alpha: 0.05      # the significance one test would have needed
    gate: false      # whether failing it refuses anything, or only says so
```

`floor` is a fixed t-stat whatever was tried. `count` divides the significance by the number of
trials, which is the one that answers *was this just the luckiest of fifty*. `t` here is the
annualised Sharpe times the square root of the years covered — so a good ratio measured over
three months is not the same evidence as the same ratio over ten years.

Off by default, and reporting-only by default. With `gate: true` the one thing it refuses is
marking a run `live` when it has not cleared; measuring is always allowed. Every change to the
bar is written to the event log with who made it, and loosening it is logged as a warning —
because whoever is proposing strategies should not be able to quietly lower the line that judges
them.

## Which part earned it

A hypothesis that names a sector and a rule that ranks inside it are two different claims, and a
replay prices them together. The one about the world lives in the **universe**; the arithmetic
lives in the **step**. Because a universe is a swappable argument, the first can be switched off
and the second held:

```
    net                          +14.2%
    same rule, base universe     +13.6%
    the idea was worth            +0.6%
```

Which is why an agent building a pipeline has to write a universe with dates, not a list of
symbols inside a script: a filter buried in code cannot be turned off, so it cannot be priced —
and a list chosen today, applied to the past, credits survivorship bias to the idea.

More in **[docs/attribution.md](https://github.com/fidetolabs/qanat/blob/main/docs/attribution.md)**.

## Sessions

The console keeps the conversations. Each one holds what was asked, what the agent did about it,
what it cost, and the replays that came out of it — and writes itself a summary when it closes,
by resuming the session and asking it what happened.

Open past sessions from **Session** in the left rail. Picking one and continuing it hands the id
back to the CLI as `--resume`, so the agent gets its own transcript rather than a paraphrase. If
the CLI no longer holds it, the summary goes into the brief instead.

A session that ran no replay simply shows none. Most sessions are a question and an answer.

## Working while you are not

```yaml
research:
  enabled: true
  schedule: 0 3 * * *
  goal: falsify        # falsify · monitor
  budget_usd: 1.0
  targets: 1
```

`falsify` takes the strategy with the fewest recorded trials and tries to **break** it — other
windows, other universes, wider and narrower rebalances, higher costs than the project assumes —
recording every attempt as it goes. It may not edit the strategy: a pass that improves the thing
it was measuring has measured nothing.

It is the one goal that cannot overfit. Everything it can produce takes confidence away.

`monitor` compares what a live strategy has earned since the frontier against what its backtest
implied, and invents nothing either.

**Searching for new strategies is deliberately not here.** A loop that proposes and keeps winners
is an overfitting machine wearing a cron expression; it needs the bar gating rather than
reporting first. What is here instead is the part that makes such a loop survivable later: the
count, and the refutations.

The budget is measured, not estimated — the CLI reports what each run cost, so a pass that turns
out expensive stops on the way through. Watch one from **Session → research → run a pass**.

## Strategies on the shelf

Four plain strategies ship with Qanat, so you have something real to replay on day one.

```bash
qanat alphas                                        # what is on the shelf
qanat alphas momentum --reads normalized.prices     # wire one up
qanat run && qanat backtest --from … --to …
```

| | |
| --- | --- |
| `momentum` | rank by trailing return, hold the top names. Long only |
| `reversal` | the same over days rather than months, buying what just fell |
| `low_vol` | hold the quietest names, sized inversely to their own volatility |
| `neutral_momentum` | momentum with the average taken out. Long and short, equal sides |

Each one writes an ordinary step file into `steps/`. Edit it, or throw it away and write your own.
**The tool is what is given away here. The strategy never is.**

## Data sources

| connector | what it is |
| --- | --- |
| `rest` | an HTTP endpoint returning JSON. `${ENV_VAR}` is expanded, so keys never enter the file |
| `sql` | any database SQLAlchemy can reach. `pip install "qanat-fdtl[sql]"` |
| `csv` | a local path or a URL |
| `synthetic` | a deterministic fake market, so a new project runs before any API key exists |

The store is one local database: a DuckDB file by default, or Postgres on localhost
(`qanat init --postgres`, or `docker compose`).

Four examples ship with it:

| | |
| --- | --- |
| `examples/equity` | synthetic prices. Runs with no network and no keys |
| `examples/fx-bundled` | **real data, in the repo.** 27 years of ECB rates, 126 KB, no key needed |
| `examples/fx-real` | the same project fetching the same rates over HTTP |
| `examples/scheduled-ingest` | a source on a clock, fetching while you watch the console |

## Status

**Beta, and the first packaged release.** It does what this page says on my own work, but nobody
else has run it yet. If something breaks or looks wrong, open an
[issue](https://github.com/fidetolabs/qanat/issues) or say so on
[Discord](https://discord.gg/JUmwATScS8).

Working end to end: the pipeline and its rules, the DuckDB and Postgres store, the console, cron
scheduling, Docker, the point-in-time replay engine with its net-edge report, the MCP server,
sessions with resume and summaries, the trial ledger and its bar, and the unattended
falsification pass.

Scoring forward is implemented: switch `live: true` on, name the alpha in `live_alphas:`, and
`qanat serve` prices a pass every time the data reaches the next rebalance date. It stamps the
frontier once, so what happens after it is the one sense of out-of-sample that cannot be arrived at
by looking.

Not implemented: backfills, incremental windows, and live trading. Qanat produces a portfolio, on
history and going forward. **It does not place an order**, and stops at the point where money
would move.

**Two limits worth knowing before you lean on the honesty machinery.** The window is not sealed:
the point-in-time views stop a *step* seeing the future, but nothing stops the agent reading past
a date while it decides what to try. And the search goal is absent — a loop that proposes
strategies and keeps the winners needs that seal, and the bar gating rather than reporting,
before it is anything other than an expensive way to fool yourself. What is here is the part that
makes such a loop survivable later: the count, and the refutations.

Qanat runs one kind of pipeline, the kind that ends in a portfolio. Airflow, Dagster and Prefect
handle arbitrary DAGs and distributed execution. Reach for those when you need them.

Issues and pull requests are welcome. See
**[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md)**.

## License

MIT License. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
