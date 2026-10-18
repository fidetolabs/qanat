# The replay engine

The README covers running a backtest and reading the number. This is the rest of how it works.


## What a replay reports

One row per rebalance date, and the totals from the rows closed so far.

| field | |
| --- | --- |
| `net` | compounded over the periods, after fees and slippage |
| `periods` | how many closed. **Read this before the number beside it** |
| `turnover` | how much had to be traded to get there |
| `max_drawdown` | the worst peak-to-trough along the path |
| `hit_rate` | the share of periods that were positive |

**`periods` is not a footnote.** A number from four rebalances and a number from four hundred are
not the same claim. The count sits next to the figure for that reason.

Removed in 0.4.0: `vol`, `sharpe`, the t-statistic, `benchmark_net`, `excess_net`, the in-sample /
out-of-sample split, the indicator IC block, and the significance bar. All of them judged a result,
and this engine reports one.

The arithmetic behind that is in the fdtl-kb record, `O-20261018-04`. An annual rebalance's sample
size is the number of years, not the number of companies, and at fourteen annual rebalances the
significance bar lands at an IC of 0.069 where a real indicator is 0.02 to 0.06. The test fails on a
true effect at any history length that is available, so printing it was worse than printing nothing.

## Point-in-time universes

A universe csv may carry `from` and `to` columns. When it does, `ctx.universe()` returns the
members **of the day the step is deciding**. Never a name before it was listed, never one that had
already been delisted. A blank `from` means since always, a blank `to` means still in.

Without those columns you get the same list every day. That is today's list applied to the past,
which is survivorship bias. The names that went bust are missing, so every number looks better
than it really was.

`qanat check` warns when a universe has no dates, and `qanat init` ships them.

## How the clock is enforced

Before each pass, every table is shadowed by a view holding only the rows that existed at that
moment, and the search path puts those views first. A step reads the past without knowing it is
being replayed, so a step that forgot to filter still cannot see the future.

A step that writes a row stamped after its own as-of date **fails**, rather than returning a
good-looking number.

## Net compounds

Each period is earned on what the last one left, so `net` agrees with the equity curve. The report
prints `sum of periods` beside it. Gross, fees and slippage are per-period amounts, and that
summed line is the one they reconcile against.

## Opening a single rebalance

`qanat report` gives you the periods. The `period` MCP tool gives you one of them
in full: what was held, what each name returned, and what was traded to get there.

The contributions add up to that period's gross, because the server recomputes them from what the
run recorded. It does not keep a second copy that could drift.

## It reports as it goes

A period needs only the portfolio decided at one stop and a price at both ends, so the replay
closes each period the moment the next pass finishes. The curve grows a point per rebalance
instead of appearing at the end.

A run that takes a minute is readable from the first few seconds, and `_qanat_bt_periods` has the
rows while it is still running. A test checks that scoring as it goes gives the same numbers as
scoring the whole set afterwards.

## A step can say how it wants to be run

`rebalance:` and `decay:` on the step are that step's own defaults. A five-day reversal and a
sixty-day momentum are not asking for the same gap.

What the run asks for wins, then the step's own, then the project's. Two priced in one basket
that disagree raise an error naming both, rather than letting one of them win silently.

## Costs are conditions of the run

`--fee-bps`, `--slippage-bps`, `--purge` and `--embargo` override `qanat.yaml` for one replay, and
they move the run's digest. Raising the fee until the edge dies is how you find out how much of the
edge was real.

## A replay puts the tables back

Each pass rewrites the derived tables from a slice of the past, so the last thing a backtest does
is one ordinary pass to restore them. A backtest never leaves your data truncated.

## Periods that held nothing are counted, and said so

A lookback longer than the warm-up, or a feature table that starts late, leaves a run holding
nothing for a stretch. Those periods are real and they belong in the money -- flat is a result.
They are not decisions, though, and averaging over them buries that: the hit rate counts them as
misses and the per-period figure divides a few real returns over many empty slots.

So `totals` carries `held_periods` and `flat_periods` alongside `hit_rate_held` and
`net_per_held_period`, and every surface that quotes the headline can qualify it. The report says
both, and neither figure is quoted
without the other. One step here reported `net +5.61%` and a `6.1%` hit rate having held a
portfolio in three periods out of thirty-three; both numbers mislead, in opposite directions.

## Live scoring is gone

`live:`, `live_steps:`, `live_from:` and the frontier that separated live from out-of-sample were
removed in 0.4.0. `D-20260904-01` dropped live from the scope and the code had not caught up.
