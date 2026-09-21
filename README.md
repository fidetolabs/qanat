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

For Claude Code, `claude mcp add qanat -- qanat mcp`. Add `--read-only` and the agent can look at
everything but change nothing.

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
scheduling, Docker, the point-in-time replay engine with its net-edge report, and the MCP server.

Scoring forward is implemented: switch `live: true` on, name the alpha in `live_alphas:`, and
`qanat serve` prices a pass every time the data reaches the next rebalance date. It stamps the
frontier once, so what happens after it is the one sense of out-of-sample that cannot be arrived at
by looking.

Not implemented: backfills, incremental windows, and live trading. Qanat produces a portfolio, on
history and going forward. It does not place an order.

**One limit worth knowing before you trust a number.** There is no benchmark. Nothing separates
your edge from the market's own move, so a long-only strategy in a rising market looks good and
the report cannot tell you why.

Qanat runs one kind of pipeline, the kind that ends in a portfolio. Airflow, Dagster and Prefect
handle arbitrary DAGs and distributed execution. Reach for those when you need them.

Issues and pull requests are welcome. See
**[CONTRIBUTING.md](https://github.com/fidetolabs/qanat/blob/main/CONTRIBUTING.md)**.

## License

MIT License. See [LICENSE](https://github.com/fidetolabs/qanat/blob/main/LICENSE).

Copyright (c) 2026 fidetolabs
