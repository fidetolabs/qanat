# The replay engine

The README covers running a backtest and reading the number. This is the rest of how it works.

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

`qanat report` gives you the periods. The console, and the `period` MCP tool, give you one of them
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

## An alpha can say how it wants to be run

`rebalance:` and `decay:` on the step are that alpha's own defaults. A five-day reversal and a
sixty-day momentum are not asking for the same gap.

What the run asks for wins, then the alpha's own, then the project's. Two alphas priced in one book
that disagree raise an error naming both, rather than letting one of them win silently.

## Costs are conditions of the run

`--fee-bps`, `--slippage-bps`, `--purge` and `--embargo` override `qanat.yaml` for one replay, and
they move the run's digest. Raising the fee until the edge dies is how you find out how much of the
edge was real.

## A replay puts the tables back

Each pass rewrites the derived tables from a slice of the past, so the last thing a backtest does
is one ordinary pass to restore them. A backtest never leaves your data truncated.
