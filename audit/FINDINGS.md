# Chaos audit — findings ledger

Everything found by breaking qanat 0.1.1 on purpose, in one list, with a status.
Full write-ups with reproduction output are in [`reports/`](reports/); the scripts
that produced them are in [`repro/`](repro/).

| Report | What it covers |
| --- | --- |
| [`reports/01-engine.html`](reports/01-engine.html) | The store, the scheduler, the replay. 25 fault injections. |
| [`reports/02-console.html`](reports/02-console.html) | The console, driven in a real browser. Every button, the form, the graph. |
| [`reports/03-workflow.html`](reports/03-workflow.html) | The real job: source → step → table → PnL, on known-answer data and seven live public APIs. |

Severity: **C** critical · **H** high · **M** medium · **L** low.
Status: **fixed** · partial · open.

**58 of 73 are fixed** as of v0.1.2, 6 are partly fixed, and 9 remain — each with a
reason below. Every fix was checked by re-running the script that found it; the
suite is green and `ruff` is clean. The one finding that cannot be closed is
recorded as a limit, not a fix:

> A step is arbitrary Python, so it can always read a source file off disk itself
> (`pd.read_csv(ctx.root / …)`) and reach data the as-of views hide. Nothing
> in-process can prevent that. The three doors that *were* qanat's to close —
> `ctx.store.read`, schema-qualified `ctx.sql`, and the dropped timezone offset —
> are closed.

---

## Correctness of the replay — an alpha must not see the future

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| W-01 | C | `ctx.store.read()` and a schema-qualified `ctx.sql("… FROM main.raw__x")` both skip the as-of views. Cheating alpha earned 1292% against an honest 3.40%. | **fixed** |
| W-02 | C | A timezone offset is dropped by `CAST(col AS TIMESTAMP)`, so a `-05:00` row is visible 4 hours early and a `+09:00` row 9 hours late. | **fixed** for a DuckDB store; on an attached Postgres the column carries its own zone and the plain cast is kept — a richer expression is rewritten on pushdown |
| W-03 | C | REST `fetched_at` lands as `TIMESTAMPTZ`, so off UTC every row is hidden from a replay for the length of the offset. 0 of 1 rows visible on Asia/Seoul. | **fixed** |
| R-01 | H | An epoch-integer clock breaks `max_time`, `read(as_of=)` and every PIT view read with `Unimplemented type for cast (BIGINT -> TIMESTAMP)`. | **fixed** |
| W-04 | H | A holding with no price earns zero, is still counted in `holdings`, and produces no note. 25% of a book became silent cash. | **fixed** |
| F-14 | L | The lookahead guard silently skips any table with no recognised time column. | open — a clockless table is still skipped by the lookahead guard |

## Input validation — a string should not become an instruction

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| F-01 | C | A table name is never validated, so it can carry SQL. `qanat check` prints "contract holds", then the step drops a real table. | **fixed** |
| F-02 | C | A step's `script:` can point outside the project (`../`, absolute), and is then imported and executed. The stub is also written before validation. | **fixed** |
| U-01 | C | A negative commission is accepted at every layer. `-9999 bps` produced `+1938%` in the strategy book with no marker. | **fixed** |
| F-15 | L | `store:` can be repointed anywhere on the filesystem in one call. | open — store path is still unconstrained |

## Silent wrong numbers

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| G-01 | C | A cycle in a features stage passes `qanat check` and multiplies feature values ×10 on every run, all steps reporting `ok`. | **fixed** |
| G-03 | H | A downstream step runs after its upstream failed, reports `ok` on stale data, and is stamped up to date in `_qanat_state`. | **fixed** |
| G-04 | H | A misspelled option leaves `${var}` literal in the SQL: 0 rows, status `ok`, no warning. | **fixed** |
| G-02 | H | Two steps may write the same weights table; the book then counts one portfolio as two alphas with identical net. | **fixed** |
| G-05 | H | A `.sql` body or `ctx.sql` can read an undeclared table, so the drawn lineage is wrong, `stale()` misses it, and `check` warns the opposite of the truth. | open — an undeclared read through raw SQL is still possible |
| W-05 | H | The digest ignores the data, so `compare` says "any difference here is the engine" when the data changed underneath. | **fixed** |
| W-16 | M | `decay` silently rescales the book (\|w\| 1.0 → 0.33 for an offsetting book). `combine()` renormalises; `decay_weights` does not. | **fixed** |
| W-17 | M | `save_bt_weights` records the pre-decay book, so "what was held" is not what was priced. | **fixed** |
| W-18 | M | The \|weights\|≠1 warning reaches only the event log — never `notes`, `conditions` or `status`. | **fixed** |
| W-19 | M | Duplicate symbols in a weights table are silently deduped by `groupby.last()`. | **fixed** |
| W-20 | M | A half-failed run is ranked in the strategy book like a complete one. | **fixed** |
| W-21 | M | A rebalance finer than the data silently skips most stops. | partial — the count is honest; no headline summary yet |
| G-06 | M | `qanat plan` reports "unchanged" after `rebalance`, `decay`, `when` or a source `key` is edited. | **fixed** |
| G-07 | M | A `when:` chain does not fire when the upstream correctly clears its table. | **fixed** |
| G-10 | L | An option named `as_of` is silently overridden during a replay. | **fixed** |

## Crash, concurrency and lifecycle

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| F-03 | H | A crash during a replay leaves the project's real tables truncated to a past date, with a backtest stuck `running` and leftover views. Nothing repairs it. | **fixed** |
| F-04 | H | During a replay the console reports 0 rows for every derived table, and scheduled jobs compute against the replay's as-of data. | partial — the scheduler no longer races a replay's state, but the console still reads truncated tables mid-replay |
| F-05 | H | Two replays can run at once: the API lock is not taken by the scheduler's live pass. Both produced 0 periods. | **fixed** |
| F-06 | H | `qanat.yaml` is written non-atomically from two threads with no backup. 167 of 400 concurrent reads failed. | **fixed** |
| F-07 | H | A rejected edit poisons the in-memory project, so every later edit fails until restart. | **fixed** |
| F-09 | M | `sys.exit()` / `KeyboardInterrupt` in a step escapes the runner, leaving the run row `running` forever. | **fixed** |
| F-13 | M | A job has no timeout and no way to cancel it. | open — no job timeout or cancel endpoint yet |
| G-09 | M | A multi-write step that fails partway leaves the tables it already wrote. | **fixed** |
| F-12 | M | Nothing bounds the size of a replay: one year at `rebalance: 1s` is 31.6M stops and 762 MB before anything runs. | **fixed** |
| F-10 | M | Retention deletes `raw` rows — the one thing a replay cannot rebuild — with no confirmation and no minimum. | **fixed** |

## Connecting a data source

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| W-07 | H | Zero-padded tickers are destroyed: KRX `005930` lands as `5930`. No `dtype` option exists; `options` is ignored by the csv connector. | **fixed** |
| W-08 | H | A reordered feed writes data into the wrong columns — the key matches by name but the insert is positional. | **fixed** |
| R-02 | H | A column that is all-null in the first response is typed forever; the second country's poll then fails on a cast. | **fixed** |
| R-03 | H | Nested JSON lands as structs with no top-level clock, so a replay never filters it and retention never expires it. | partial — warned, not inferred — a clock inside a struct still has to be flattened by a step |
| W-09 | M | `mode: append` is the default and doubles the table on every poll when no `key` is set. Confirmed on live ECB data: 23 → 69 rows. | **fixed** |
| W-10 | M | A null in a key column defeats dedup forever. | **fixed** |
| W-11 | M | One bad symbol loses the whole per-symbol REST batch. | **fixed** |
| W-12 | M | `mode: replace` never clears when the feed returns nothing. | **fixed** |
| W-13 | M | A float landing in a column pandas first typed as int is silently rounded. | **fixed** |
| W-14 | M | A 200 response with the wrong shape replaces the price history. | partial — append is guarded by name; a `replace` with a disjoint shape still lands |
| W-15 | M | An unset `${VAR}` becomes an empty string; lowercase `${var}` is never expanded. | **fixed** |
| R-04 | M | A `Year` column is not recognised as a clock, so real annual data is never filtered or expired. | **fixed** |
| R-05 | M | A JSON body that is a single object cannot be ingested. | **fixed** |
| R-06 | M | A body that is `[metadata, rows]` cannot be ingested: `records:` has no array index. | **fixed** |
| R-07 | M | `payload: true` rescues both, but the docs point it at a case that works without it. | open — docs only |
| R-08 | M | A slow public API times out inside the default 30s with no hint that `options.timeout` exists. | **fixed** |

## The journey and the contract

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| W-06 | H | The console's own "add an alpha" flow makes an alpha that cannot run; `check` says ok; the backtest returns HTTP 200 with 37 failures. | **fixed** |
| G-08 | M | A features stage can sit after `weights` — but only when a `pnl` stage exists (the check is on an `elif`). | **fixed** |
| F-11 | M | Superseded by G-01. | **fixed** |

## Console — keyboard, state and errors

| ID | Sev | Finding | Status |
| --- | --- | --- | --- |
| U-02 | C | The pipeline graph is a bare `<canvas>` with no keyboard path. 15 tab stops on the whole page; the canvas is not one of them. | **fixed** |
| U-03 | H | A hung server still reads "connected" with stale numbers: no fetch has a timeout. | **fixed** |
| U-04 | H | With the server down and the graph folded, the run form opens completely blank. | **fixed** |
| U-05 | H | The backtest-list error handler writes to `#bt-list`, which does not exist. | **fixed** |
| U-06 | H | One failed request stops live progress permanently — both early returns skip the reschedule. | **fixed** |
| U-07 | H | A bad `rebalance` returns HTTP 500, and the log names the wrong field ("retention must look like…"). | **fixed** |
| U-08 | H | Delete has no confirmation and sits next to save. | **fixed** |
| U-11 | H | No visible focus indicator anywhere; the one `:focus` rule removes the outline. | **fixed** |
| U-12 | H | Folded panels keep their buttons in the tab order. | open — folded panels still keep their buttons in the tab order |
| U-13 | H | `edit` on an alpha card is hover-only and nested inside a button — no keyboard path at all. | open — `edit` on an alpha card is still hover-only |
| U-17 | H | The run can be submitted twice, and the 409 is erased by the next repaint. | partial — the 409 now survives the repaint; the button is still re-enabled on reopen |
| U-09 | M | The run form is not a dialog: no role, no focus move, no trap. | partial — closes on Escape and the backdrop; no focus trap yet |
| U-10 | M | Escape closes the panel behind the form instead of the form. | **fixed** |
| U-14 | M | 11 of 21 form inputs have no associated label. | **fixed** |
| U-15 | M | Table sorting is bound to `<th>` and needs a mouse. | open — table sorting is still bound to <th> |
| U-16 | M | Chart drill-down and range selection are pointer-only. | open — chart drill-down is still pointer-only |
| U-18 | M | Errors are shown as raw JSON (`{"detail": …}`). | **fixed** |
| U-19 | M | Two polling loops overlap; none back off. | **fixed** |
| U-20 | L | Dead affordances (`cursor: pointer` on rows with no handler) and no `prefers-reduced-motion` guard. | **fixed** |

---

## Confirmed correct — do not regress these

- Net, gross, turnover, fees and slippage match hand-computed answers exactly (net to 1.6e-13 over 143 periods).
- The fill rule: a portfolio decided at *t* is filled at the next price, never the one that produced it.
- `purge` and `embargo` shift by exactly the duration given.
- Blend allocation arithmetic is exact; zero and negative allocations are refused with clear messages.
- The digest catches script edits and option changes; `seed` makes a random alpha repeatable.
- `runner.order()` sorts correctly for any declaration order, including fully reversed.
- `ctx.read()` and unqualified SQL are correctly blocked by the as-of views.
- Unicode (Korean) survives source → store → step → report unchanged.
- `qanat plan` on a rename is exact; `stale()` propagates the full length of a chain.
- PnL tables written by a replay are correctly protected from `prune`.
- All 34 console buttons are real `<button>` elements with real text; every `fetch()` checks its status.
