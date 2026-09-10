# Findings

Everything the [chaos audit](README.md) has found, and where each one stands.
Open and partly fixed come first, because those are the only rows anybody needs to
read. The closed ones are kept — an id is cited in a published report, a commit
message and a test name — but folded away.

Severity: **C** critical · **H** high · **M** medium · **L** low.

| | |
| --- | --- |
| **Fixed** | 67 |
| **Partly fixed** | 6 |
| **Open** | 0 |
| **Total** | 73 |

Every fix was checked by re-running the script that found it, not by re-reading the
code. Each one also has a case in `tests/test_audit_fixes.py`, named for its id, so
it fails loudly if it comes back.

## Two limits, stated rather than hidden

**A step can always read a source file off disk.** `pd.read_csv(ctx.root / …)`
reaches the whole history, and nothing in this process can prevent it — a step is
arbitrary Python. The three doors that *were* qanat's to close are closed, and
`batteries/test_leak.py` asserts this one still leaks, so the day it stops somebody
will notice.

**A running Python thread cannot be ended.** The job timeout frees the worker and
closes the run row; the work itself finishes on its own and its result is discarded.
That is what "fixed" means for **F-13**, and the message the user sees says so.

## Still open or partly fixed

| ID | Sev | Finding | What is left |
| --- | --- | --- | --- |
| F-04 | H | During a replay the console reports 0 rows for every derived table, and scheduled jobs compute against the replay's as-of data. | the scheduler no longer races a replay's state, but the console still reads truncated tables mid-replay |
| R-03 | H | Nested JSON lands as structs with no top-level clock, so a replay never filters it and retention never expires it. | warned, not inferred — a clock inside a struct still has to be flattened by a step |
| U-17 | H | The run can be submitted twice, and the 409 is erased by the next repaint. | the 409 now survives the repaint; the button is still re-enabled on reopen |
| W-21 | M | A rebalance finer than the data silently skips most stops. | the count is honest; no headline summary yet |
| W-14 | M | A 200 response with the wrong shape replaces the price history. | append is guarded by name; a `replace` with a disjoint shape still lands |
| U-09 | M | The run form is not a dialog: no role, no focus move, no trap. | closes on Escape and the backdrop; no focus trap yet |

## Closed

<details>
<summary><b>Correctness of the replay — an alpha must not see the future</b> — 6 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| W-01 | C | `ctx.store.read()` and a schema-qualified `ctx.sql("… FROM main.raw__x")` both skip the as-of views. Cheating alpha earned 1292% against an honest 3.40%. | — |
| W-02 | C | A timezone offset is dropped by `CAST(col AS TIMESTAMP)`, so a `-05:00` row is visible 4 hours early and a `+09:00` row 9 hours late. | for a DuckDB store; on an attached Postgres the column carries its own zone and the plain cast is kept — a richer expression is rewritten on pushdown |
| W-03 | C | REST `fetched_at` lands as `TIMESTAMPTZ`, so off UTC every row is hidden from a replay for the length of the offset. 0 of 1 rows visible on Asia/Seoul. | — |
| R-01 | H | An epoch-integer clock breaks `max_time`, `read(as_of=)` and every PIT view read with `Unimplemented type for cast (BIGINT -> TIMESTAMP)`. | — |
| W-04 | H | A holding with no price earns zero, is still counted in `holdings`, and produces no note. 25% of a book became silent cash. | — |
| F-14 | L | The lookahead guard silently skips any table with no recognised time column. | — |

</details>

<details>
<summary><b>Input validation — a string should not become an instruction</b> — 4 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| F-01 | C | A table name is never validated, so it can carry SQL. `qanat check` prints "contract holds", then the step drops a real table. | — |
| F-02 | C | A step's `script:` can point outside the project (`../`, absolute), and is then imported and executed. The stub is also written before validation. | — |
| U-01 | C | A negative commission is accepted at every layer. `-9999 bps` produced `+1938%` in the strategy book with no marker. | — |
| F-15 | L | `store:` can be repointed anywhere on the filesystem in one call. | a warning, since a store on another disk is a real choice |

</details>

<details>
<summary><b>Silent wrong numbers</b> — 14 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| G-01 | C | A cycle in a features stage passes `qanat check` and multiplies feature values ×10 on every run, all steps reporting `ok`. | — |
| G-03 | H | A downstream step runs after its upstream failed, reports `ok` on stale data, and is stamped up to date in `_qanat_state`. | — |
| G-04 | H | A misspelled option leaves `${var}` literal in the SQL: 0 rows, status `ok`, no warning. | — |
| G-02 | H | Two steps may write the same weights table; the book then counts one portfolio as two alphas with identical net. | — |
| G-05 | H | A `.sql` body or `ctx.sql` can read an undeclared table, so the drawn lineage is wrong, `stale()` misses it, and `check` warns the opposite of the truth. | — |
| W-05 | H | The digest ignores the data, so `compare` says "any difference here is the engine" when the data changed underneath. | — |
| W-16 | M | `decay` silently rescales the book (\|w\| 1.0 → 0.33 for an offsetting book). `combine()` renormalises; `decay_weights` does not. | — |
| W-17 | M | `save_bt_weights` records the pre-decay book, so "what was held" is not what was priced. | — |
| W-18 | M | The \|weights\|≠1 warning reaches only the event log — never `notes`, `conditions` or `status`. | — |
| W-19 | M | Duplicate symbols in a weights table are silently deduped by `groupby.last()`. | — |
| W-20 | M | A half-failed run is ranked in the strategy book like a complete one. | — |
| G-06 | M | `qanat plan` reports "unchanged" after `rebalance`, `decay`, `when` or a source `key` is edited. | — |
| G-07 | M | A `when:` chain does not fire when the upstream correctly clears its table. | — |
| G-10 | L | An option named `as_of` is silently overridden during a replay. | — |

</details>

<details>
<summary><b>Crash, concurrency and lifecycle</b> — 9 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| F-03 | H | A crash during a replay leaves the project's real tables truncated to a past date, with a backtest stuck `running` and leftover views. Nothing repairs it. | — |
| F-05 | H | Two replays can run at once: the API lock is not taken by the scheduler's live pass. Both produced 0 periods. | — |
| F-06 | H | `qanat.yaml` is written non-atomically from two threads with no backup. 167 of 400 concurrent reads failed. | — |
| F-07 | H | A rejected edit poisons the in-memory project, so every later edit fails until restart. | — |
| F-09 | M | `sys.exit()` / `KeyboardInterrupt` in a step escapes the runner, leaving the run row `running` forever. | — |
| F-13 | M | A job has no timeout and no way to cancel it. | the worker is freed and the run closed; a running Python thread still cannot be killed |
| G-09 | M | A multi-write step that fails partway leaves the tables it already wrote. | — |
| F-12 | M | Nothing bounds the size of a replay: one year at `rebalance: 1s` is 31.6M stops and 762 MB before anything runs. | — |
| F-10 | M | Retention deletes `raw` rows — the one thing a replay cannot rebuild — with no confirmation and no minimum. | — |

</details>

<details>
<summary><b>Connecting a data source</b> — 14 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| W-07 | H | Zero-padded tickers are destroyed: KRX `005930` lands as `5930`. No `dtype` option exists; `options` is ignored by the csv connector. | — |
| W-08 | H | A reordered feed writes data into the wrong columns — the key matches by name but the insert is positional. | — |
| R-02 | H | A column that is all-null in the first response is typed forever; the second country's poll then fails on a cast. | — |
| W-09 | M | `mode: append` is the default and doubles the table on every poll when no `key` is set. Confirmed on live ECB data: 23 → 69 rows. | — |
| W-10 | M | A null in a key column defeats dedup forever. | — |
| W-11 | M | One bad symbol loses the whole per-symbol REST batch. | — |
| W-12 | M | `mode: replace` never clears when the feed returns nothing. | — |
| W-13 | M | A float landing in a column pandas first typed as int is silently rounded. | — |
| W-15 | M | An unset `${VAR}` becomes an empty string; lowercase `${var}` is never expanded. | — |
| R-04 | M | A `Year` column is not recognised as a clock, so real annual data is never filtered or expired. | — |
| R-05 | M | A JSON body that is a single object cannot be ingested. | — |
| R-06 | M | A body that is `[metadata, rows]` cannot be ingested: `records:` has no array index. | — |
| R-07 | M | `payload: true` rescues both, but the docs point it at a case that works without it. | — |
| R-08 | M | A slow public API times out inside the default 30s with no hint that `options.timeout` exists. | — |

</details>

<details>
<summary><b>The journey and the contract</b> — 3 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| W-06 | H | The console's own "add an alpha" flow makes an alpha that cannot run; `check` says ok; the backtest returns HTTP 200 with 37 failures. | — |
| G-08 | M | A features stage can sit after `weights` — but only when a `pnl` stage exists (the check is on an `elif`). | — |
| F-11 | M | Superseded by G-01. | — |

</details>

<details>
<summary><b>Console — keyboard, state and errors</b> — 17 fixed</summary>

| ID | Sev | Finding | Note |
| --- | --- | --- | --- |
| U-02 | C | The pipeline graph is a bare `<canvas>` with no keyboard path. 15 tab stops on the whole page; the canvas is not one of them. | — |
| U-03 | H | A hung server still reads "connected" with stale numbers: no fetch has a timeout. | — |
| U-04 | H | With the server down and the graph folded, the run form opens completely blank. | — |
| U-05 | H | The backtest-list error handler writes to `#bt-list`, which does not exist. | — |
| U-06 | H | One failed request stops live progress permanently — both early returns skip the reschedule. | — |
| U-07 | H | A bad `rebalance` returns HTTP 500, and the log names the wrong field ("retention must look like…"). | — |
| U-08 | H | Delete has no confirmation and sits next to save. | — |
| U-11 | H | No visible focus indicator anywhere; the one `:focus` rule removes the outline. | — |
| U-12 | H | Folded panels keep their buttons in the tab order. | — |
| U-13 | H | `edit` on an alpha card is hover-only and nested inside a button — no keyboard path at all. | — |
| U-10 | M | Escape closes the panel behind the form instead of the form. | — |
| U-14 | M | 11 of 21 form inputs have no associated label. | — |
| U-15 | M | Table sorting is bound to `<th>` and needs a mouse. | — |
| U-16 | M | Chart drill-down and range selection are pointer-only. | — |
| U-18 | M | Errors are shown as raw JSON (`{"detail": …}`). | — |
| U-19 | M | Two polling loops overlap; none back off. | — |
| U-20 | L | Dead affordances (`cursor: pointer` on rows with no handler) and no `prefers-reduced-motion` guard. | — |

</details>

---

## Confirmed correct — do not regress these

The half of an audit that finds nothing is worth as much as the half that does, and
these are why `batteries/` exists.

- Net, gross, turnover, fees and slippage match hand-computed answers exactly — net
  to 1.6e-13 over 143 periods.
- The fill rule: a portfolio decided at *t* is filled at the next price, never the
  one that produced it.
- `purge` and `embargo` shift by exactly the duration given.
- Blend allocation arithmetic is exact; zero and negative allocations are refused
  with clear messages.
- The digest catches script edits and option changes; `seed` makes a random alpha
  repeatable.
- `runner.order()` sorts correctly for any declaration order, including fully
  reversed.
- Unicode survives source → store → step → report unchanged.
- `qanat plan` on a rename is exact; `stale()` propagates the length of a chain.
- PnL tables written by a replay are protected from `prune`.

## A regression this audit caught in its own fix

The 0.1.2 fix for **U-06** — live progress stopping for good after one failed
request — put `fetchTimeout` and `backoff` in the wrong one of `backtests.js`'s two
IIFEs. `tick` threw `ReferenceError: backoff is not defined` on its first call, its
`catch` threw the same error again, and nothing ever rescheduled. The poller was
dead from page load, which is precisely what that fix was for.

It was found the same way everything else here was: by opening the console in a
browser and reading what it actually did. Fixed in 0.1.3, and now checked by
counting the requests the poller makes rather than the code that should make them.

## Naming

New findings get a flat id that is never reused, continuing from **QA-074**. The
prefixes above (`F-`, `W-`, `U-`, `G-`, `R-`) are from the first run, before it was
clear this would repeat; they are kept because they are cited in published reports,
in commit messages and in 36 test names.
