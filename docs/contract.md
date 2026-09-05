# The stage contract

`qanat check` enforces every one of these, and refuses to serve a project that breaks one
unless you pass `--force`.

## 1. Nothing writes into a `raw` stage except a source

Raw is landed exactly as it arrived. If a row was wrong when it came in, it stays wrong in
raw, and the fix lives in the next stage where it can be seen. If a step may edit raw, the
only record of what the source actually sent is gone.

## 2. Data only moves forward

A step may read from a stage before its target, or from its own stage when the target is a
`features` stage — that is a feature chain, and it is the normal way features are built. It
may never read from a later one. A pipeline that reaches backwards has a cycle in it, and a
cycle in a scheduled pipeline is a pipeline whose output depends on when you last ran it.

## 3. One `weights` stage, last — unless a `pnl` stage follows it

The end of a feature pipeline is a portfolio: a target weight per symbol.

A project may add one `pnl` stage after `weights`, and then that one is last. Nothing writes
into it by hand: `qanat backtest` does, once it knows what the portfolio was worth. It holds
what a run earned, per rebalance, so the last node of the graph is a real table you can query
with everything else rather than a number that only exists inside a report.

## 4. One step writes one weights table

**One alpha is one portfolio.** The weights stage holds one table per alpha, and a step that
tried to write two would be claiming to be two alphas at once.

## 5. No alpha reads another alpha's weights

An alpha reads features. It never reads another alpha, because an edge counted through a
tower of them is counted twice.

Several alphas *can* be priced together as one book — that is what `qanat backtest --alpha
a,b` does — but the combining happens in the replay, where the shares are visible and the
result is one PnL table fed by both weights tables. It never happens inside a step, where it
would be invisible.

Weights carry **no budget and no share counts**. `budget × weight ÷ price` is a question for
the system that places orders, and it depends on the venue's lot size, not on the feature.
Keeping size out of this stage is what lets the same weights table drive a backtest and a
live account without either one lying to the other.

Qanat also checks the shape at runtime: the table needs `symbol` and `weight`, the absolute
weights should sum to 1, and every symbol should be inside the step's universe. Each of those
is a warning in the log, not a failure.

## 6. Every table that is read has a producer

A step that reads `features.x` when nothing writes `features.x` fails at `qanat check`
rather than at the next scheduled run. A `.sql` step writes exactly one table, because one
`SELECT` produces one result.

## What `check` warns about but allows

**A universe with no membership dates.** A universe csv may carry `from` and `to` columns,
and then membership is point-in-time: a symbol counts on a date only if it had joined by then
and had not yet left. Without them the list is the same on every day — today's list applied
to the past — and every backtest built on it is flattered by the names that survived. That is
a warning rather than an error, because a static list is still runnable and sometimes it is
all you have. It should never be silent.

See **[words.md](words.md)** for what each word in a project file means.

## What is deliberately not in the contract

- **How many stages.** Five is the starter; two or seven are equally valid.
- **What a feature means.** Qanat does not inspect column values.
- **Table or column names.** Beyond `symbol` and `weight` in the final table, nothing is reserved.
