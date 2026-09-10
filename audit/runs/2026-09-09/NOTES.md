# Run 2026-09-09 — the first one

Three surfaces, 73 findings, released as 0.1.2 and 0.1.3.

## What was covered

| surface | how | report |
| --- | --- | --- |
| the engine | 25 fault injections against the store, scheduler and replay | [report-01-engine.html](report-01-engine.html) |
| the console | driven in a real browser: every button, the form, the graph, the tab order, a paused server and a dead one | [report-02-console.html](report-02-console.html) |
| the workflow | source → step → table → PnL, on data with a known answer and on seven live public APIs | [report-03-workflow.html](report-03-workflow.html) |

## What worked, as a method

**Known-answer data.** A backtester cannot be checked by looking at its output,
because a wrong number and a right one look identical. Building the data first —
every symbol compounding at exactly 0.1% a day — turned "is this right" into
arithmetic. Net matched `(1.001) ** 143 - 1` to 1.6e-13, and that result was worth
as much as any finding: it said the hard part was already done.

**A discriminator for leakage.** Two symbols, one up 2% and one down 2% each day,
in a pattern with nothing to learn from the past. A blind alpha earns ~3%; an alpha
that can see two days ahead earns ~1292%. Every cheat vector then runs the same
logic and differs only in how it reaches the data, so the net *is* the verdict. This
found two open doors that reading the code had not.

**Actually opening the browser.** Every console finding came from driving it, not
from reading it — including the one regression this audit introduced into its own
fix, where the live-progress poller was dead from page load.

**Re-running the finder script as the only proof.** Not "the test passes" — the
script that found it, run again, now reporting the honest number.

## What I got wrong

- Marked **R-02** and **W-13** fixed when only the workaround was in place. Verifying
  showed the store still typed an all-null column INTEGER and still rounded 102.5 to
  102. Caught by re-running rather than by re-reading.
- The first CI run failed: the offset-aware timestamp SQL used DuckDB-only functions,
  which get pushed down into an attached Postgres. Local runs skip the Postgres suite,
  so CI was the only place this could show.
- Shipped a regression in 0.1.2 — the live-progress fix put its helpers in the wrong
  one of `backtests.js`'s two IIFEs, so the poller threw on its first call and never
  rescheduled. Found in the next run's browser pass.

## What was left

Six findings are partly fixed; `../../FINDINGS.md` says what remains of each. The
largest is replay isolation (**F-04**): a replay still rewrites the live tables, so
the console reads truncated ones while it runs.

## Probes

`probes/` holds the scripts this run was made of, as the record of how each number
was produced. They were written against an ad-hoc harness that lived in a scratch
directory and **they do not run as they stand** — `../../lab/` is the successor,
and everything worth keeping from them is either there or in `batteries/`.
