# Changelog

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
