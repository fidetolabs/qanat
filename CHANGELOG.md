# Changelog

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
