# What to try

The catalogue. Each entry is a way of putting pressure on the tool, what it has
caught, and whether it is now automatic. Read it before a run and spend the time
on the rows that say **not automated** — the rest are already watching themselves.

Add a row whenever a run finds something a row would have caught. That is how this
stops being a list of what one person thought of once.

---

## Can a step see the future

The one question that cannot be answered by reading code, because a leaking alpha
looks like a brilliant one. Build data where seeing ahead is worth thousands of
per cent and being blind is worth nothing, then let the net be the verdict.

| try | caught | automated |
| --- | --- | --- |
| `ctx.read`, `ctx.sql`, `ctx.store.read`, schema-qualified names | W-01: two doors open, 1292% against an honest 3.40% | `batteries/test_leak.py` |
| a timezone offset on the clock column | W-02: `-05:00` visible four hours early | `tests/test_audit_fixes.py` |
| the connector's own `fetched_at`, on a machine that is not UTC | W-03: every row hidden for the length of the offset | `tests/` |
| reading the source file off disk | the known boundary — a step is arbitrary Python | `batteries/test_leak.py` (asserts it *does* leak) |
| a table with no clock at all | F-14: the as-of views and the guard both skip it | reported in `conditions.no_clock` |

**Not automated:** a step that writes its own timestamp; a `when:` chain that fires
mid-replay; an alpha reading another alpha's PnL table.

## Is the arithmetic the arithmetic

Data with a known answer. `(1 + rate) ** n - 1` is not a matter of opinion.

| try | caught | automated |
| --- | --- | --- |
| a constant drift, one holding | nothing — matched to 1.6e-13 | `batteries/test_arithmetic.py` |
| alternating holdings with fees | nothing — turnover, fees, slippage all exact | same |
| a blend with an allocation | nothing — exact | same |
| a name that stops being priced | W-04: earned zero, still counted as held, no note | same |
| a book that flips sign, with decay | W-16: rescaled to a third of the size the alpha asked for | `tests/` |
| `purge` and `embargo` | nothing — both shift by exactly what they say | `batteries/` |

**Not automated:** a price of zero or below (F-08 — dropped now, but the report has
no data-quality block yet); a rebalance finer than the data.

## Landing data from outside

Synthetic fixtures never produce what a real feed does.

| try | caught | automated |
| --- | --- | --- |
| a body that is one object, not a list | R-05: could not be landed at all | `batteries/test_websources.py` |
| a body that is `[metadata, rows]` | R-06: `records:` had no array index | same |
| parallel arrays | R-07: the docs sent people to an escape hatch they did not need | same |
| epoch seconds and milliseconds | R-01: raised on every as-of read | same + `tests/` |
| a `Year` column | R-04: not recognised, so never filtered | `tests/` |
| a zero-padded ticker | W-07: `005930` became `5930` | `batteries/` |
| the same feed for a second country | R-02: a null-in-run-one column was typed for ever | `tests/` |
| a feed that reorders its columns | W-08: positional insert, dates into the symbol column | `tests/` |
| polling twice with no `key` | W-09: 23 real ECB rates became 69 rows | `tests/` |

**Not automated:** a feed that changes its shape *and* keeps its status 200 (W-14);
pagination; a source that is slow rather than down.

## Building the graph

| try | caught | automated |
| --- | --- | --- |
| a loop between two steps in one stage | G-01: `check` passed, values grew ×10 per run | `tests/` |
| two steps writing one table | G-02: one portfolio counted as two alphas | `tests/` |
| a `${var}` with no option behind it | G-04: 0 rows, status ok | `tests/` |
| a `.sql` body reading an undeclared table | G-05: the drawn graph was not the real one | `tests/` |
| a step whose upstream failed | G-03: recomputed from yesterday, reported ok | `tests/` |
| steps declared in reverse order | nothing — the sort is correct | `tests/test_pipeline.py` |

**Not automated:** a `when:` chain that fires on a cleared table; a stage inserted
between weights and pnl.

## Crashing it

| try | caught | automated |
| --- | --- | --- |
| SIGKILL mid-replay | F-03: tables left truncated, nothing repaired them | manual — needs a subprocess |
| two replays at once | F-05: both returned nothing, no error | `tests/` |
| two threads writing `qanat.yaml` | F-06: 167 of 400 reads got a torn file | `tests/` |
| `sys.exit()` inside a step | F-09: the run row said `running` for ever | `tests/` |
| a job that never returns | F-13: held its worker until the process died | `tests/` |
| a rejected edit, then a valid one | F-07: the console stayed broken until restart | `tests/` |

**Not automated:** the SIGKILL case, and a full disk.

## Driving the console

Needs a browser. Nothing here is caught by a Python test, which is why the console
findings were the ones that survived longest.

| try | caught |
| --- | --- |
| walk the tab order and count the stops | U-02: the graph had none; 15 stops for the whole page |
| look for a focus ring | U-11: one `:focus` rule, and it removed the outline |
| fold a panel, then keep tabbing | U-12: every hidden control still in the order |
| press Escape over a modal | U-10: it closed the panel behind it |
| pause the server with SIGSTOP | U-03: still read "connected", with stale numbers |
| kill it, then open a modal | U-04: the modal opened completely blank |
| submit a form twice | U-17: the error was erased by the next repaint |
| type a negative number into a cost | U-01: +1938% in the strategy book |
| read the console log after every click | the `backoff` regression in 0.1.2 |

**Not automated:** all of it. A browser pass is the standing cost of each run.

---

## What a run looks like

1. `uv run pytest audit/batteries -m audit`. Green means the covered ground holds.
2. Pick the **not automated** rows above. Those are where the findings are.
3. Drive the console in a browser. It is the surface with the least automation and
   it has never come back clean.
4. Every finding: a case in `tests/test_audit_fixes.py`, named for its id.
5. Every new discriminator: a suite in `batteries/`.
6. Every lesson: a row here.
