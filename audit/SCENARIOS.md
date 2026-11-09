# What to try

The catalogue. Each entry is a way of putting pressure on the tool, what it has
caught, and whether it is now automatic. Read it before a run and spend the time
on the rows that say **not automated** — the rest are already watching themselves.

Add a row whenever a run finds something a row would have caught. That is how this
stops being a list of what one person thought of once.

---

## Can a step see the future

The one question that cannot be answered by reading code, because a leaking step
looks like a brilliant one. Build data where seeing ahead is worth thousands of
per cent and being blind is worth nothing, then let the net be the verdict.

> **`batteries/test_leak.py` and `batteries/test_arithmetic.py` were removed on
> 2026-11-09**, with the `lab/` that generated their prices. The rows below are kept
> as the record of what each try caught; the ones whose *automated* column names
> either file are **no longer run by anything**. A `tests/` entry still is.

| try | caught | automated |
| --- | --- | --- |
| `ctx.read`, `ctx.sql`, `ctx.store.read`, schema-qualified names | W-01: two doors open, 1292% against an honest 3.40% | `batteries/test_leak.py` |
| a timezone offset on the clock column | W-02: `-05:00` visible four hours early | `tests/test_audit_fixes.py` |
| the connector's own `fetched_at`, on a machine that is not UTC | W-03: every row hidden for the length of the offset | `tests/` |
| reading the source file off disk | the known boundary — a step is arbitrary Python | `batteries/test_leak.py` (asserts it *does* leak) |
| a table with no clock at all | F-14: the as-of views and the guard both skip it | reported in `conditions.no_clock` |

**Not automated:** a step that writes its own timestamp; a `when:` chain that fires
mid-replay; a step reading another step's PnL table.

## Does the real upstream still answer in the shape we parse

Nothing generated. Every row comes off EDGAR or Yahoo, because the things that
break this product are decisions somebody else made about their own feed.

| try | caught | automated |
| --- | --- | --- |
| a CIK as a ticker, a bare number, and already padded | the zeros are part of the key — a short CIK stops joining | `batteries/test_websources.py` |
| a filer with more than 1000 filings (Walmart) | `recent` caps at 1000; the older 10-Ks are only on `filings.files` | same |
| `filingDate` against `reportDate`, every 10-K | the as-of promise, on the real calendar | same |
| a company that split twice (NVDA 4:1, 10:1) | `close` is restated, so a `pe` against a filed EPS is wrong by the split factor | same |
| a KRX ticker (`005930.KS`) | zeros in the symbol, and a price in won that no symbol tells you | same |

**Not automated:** a form other than 10-K/10-Q reaching the parser; a filer that
changes CIK; an endpoint that answers 200 with an error body.

## Is the arithmetic the arithmetic

Data with a known answer. `(1 + rate) ** n - 1` is not a matter of opinion --
**and that turned out to be the problem.** Every row here was generated, so a
green battery only ever said the generator and the engine agreed on compound
interest. None of it could fail for a reason a filing would also cause.

| try | caught | automated |
| --- | --- | --- |
| a constant drift, one holding | nothing — matched to 1.6e-13 | `batteries/test_arithmetic.py` |
| alternating holdings with fees | nothing — turnover, fees, slippage all exact | same |
| a blend with an allocation | nothing — exact | same |
| a name that stops being priced | W-04: earned zero, still counted as held, no note | same |
| a book that flips sign, with decay | W-16: rescaled to a third of the size the step asked for | `tests/` |
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
| two steps writing one table | G-02: one portfolio counted as two steps | `tests/` |
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
| a rejected edit, then a valid one | F-07: the project stayed broken until restart | `tests/` |

**Not automated:** the SIGKILL case, and a full disk.

## Driving the MCP surface

There is no browser pass any more: the console is gone and MCP is the only door, so
the whole surface is reachable from a Python test. The rows that used to live here
were about a page — tab order, focus rings, modals — and they went with it.

What replaced them is a client that is not the test suite. A real agent connected
at `--scope full` chooses its own order, sends its own arguments, and that is still
where a schema that lies about a field shows up.

| try | caught |
| --- | --- |
| connect at each of the three scopes and count the tools | a tool that answers above its scope |
| call every write tool with a field the schema does not declare | an argument honoured but undocumented |
| ask an agent to add a step with no prior instruction | a flow that validates and then cannot run |
| pull the store out from under a live session | a tool that reports success on a closed database |

**Not automated:** the last two. An agent pass is the standing cost of each run.

---

## What a run looks like

1. `uv run pytest audit/batteries -m audit`. Green means the covered ground holds.
2. Pick the **not automated** rows above. Those are where the findings are.
3. Drive the MCP surface with a real agent client, not the suite. It is the
   surface with the least automation.
4. Every finding: a case in `tests/test_audit_fixes.py`, named for its id.
5. Every new discriminator: a suite in `batteries/`.
6. Every lesson: a row here.
