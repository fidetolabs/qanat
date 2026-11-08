# qanat — for agents

A data platform for processing metrics from filings. It reads company filings, computes the
metrics you define, and answers **as of any past date**. Open source, delivered as an MCP server.

**What it is not:** a backtesting engine, and not a filings product. Everything that judged a result
— the significance bar, the trial ledger, indicator scores, run comparison — was removed in 0.4.0
and is not coming back. The SEC corpus is the first dataset, not the subject.

**Nothing here places an order.** That is a standing commitment, not a missing feature.

## Run these

```sh
uv run pytest -q -n auto --dist loadgroup      # the suite
uv run ruff check src tests audit              # audit too, not just src tests
uv run pytest audit/batteries -m audit         # opt-in: slow, and one battery calls a live API
```

**The command line is five verbs and that is deliberate.** `init`, `check`,
`mcp`, `serve`, `schedule`. `ls`, `graph`, `plan`, `prune`, `run` and `screen`
were removed in 0.10.0 -- each was a person's door onto a question a tool already
answers, which is a second place the behaviour can quietly differ. Running one
prints where it went. **There is no `migrate`:** a 0.10.x project is refused at
load with the keys it got wrong named.

**`qanat mcp` is stdio and `qanat serve` is the HTTP server, because `--http`
changed what the program was.** Over stdio the process is the client's child --
no port, and a clock cannot live in it. **The verb names the program and a flag
only ever adds a door**, never subtracts, so each arrangement has one spelling:

    qanat mcp                      stdio, the agent's child
    qanat serve --mcp              /mcp only
    qanat serve --app              the pages only, nothing at /mcp
    qanat serve --mcp --app        both, one program
    qanat schedule                 the clock alone, no port

**Doors are free and programs are not.** Two doors in one process share a store
and one lock; two `qanat serve` on one project is the thing that breaks.

`CONTRIBUTING.md` has setup, the architecture table and how to add a connector. Read it instead of
reading `src/` — it is two dozen modules and you need two of them.

## The rules that are not style

**Each one is held by a test. If you break the rule, the named test fails — that is how this file
stays true.** The table is the contract; the test is the enforcement.

| Rule | Held by |
| --- | --- |
| **No reading tool loads a whole table.** A tool asks for the rows it wants. Filtering in pandas after the rows cross into Python is the bug, not the style. | `test_no_reading_tool_loads_a_whole_table` · `test_reading.py` |
| **A read can never see past the as-of date**, filtered or not. This is the product's whole claim. | `test_a_filtered_read_still_cannot_see_past_the_as_of_date` · `test_reading.py` |
| **A tool is on the surface because a flow needs it, and an entity is not a flow.** No tool name contains `job`, `definition`, `schedule` or `execution`; every tool is traced to a caller's act in `docs/flows.md`; the count at each scope may not grow. | `test_no_tool_is_named_after_an_entity` · `test_every_tool_traces_to_a_written_flow` · `test_the_count_at_each_scope_has_not_grown` · `test_the_surface.py` |
| **Every tool names one of four scopes, and the scopes nest.** A tool declared without one is refused at import. | `test_every_tool_names_one_of_the_scopes` · `test_a_tool_cannot_be_declared_without_a_scope` · `test_the_scopes_nest` · `test_mcp.py` |
| **The default scope is not the widest one.** Nothing that rewires the pipeline is offered to a caller who did not ask for `authoring`. | `test_the_default_scope_is_not_the_widest_one` · `test_mcp.py` |
| **A section vocabulary belongs to one form, and a form without one gets no spans.** `split` is told the form; it never falls through to the 10-K's. | `test_a_form_with_no_vocabulary_returns_an_empty_span_list` · `test_split_cannot_be_called_without_a_form` · `test_sections.py` |
| **A filing is ingestable because of the taxonomy its facts carry, never because of EDGAR's flag.** The flag is true for 29.8% of filings and means financial statements in 1.2%. | `test_no_flag_of_edgars_is_read_anywhere_behind_the_label` · `test_filings.py` |
| **A metric that is a series is a transform definition.** `define_metric` writes one into the metrics table, keyed so a corrected input adds a row rather than replacing the value somebody was shown. A formula taking the date as a parameter is not a series and is not materialised. | `test_defining_a_series_writes_a_transform_and_a_script` · `test_a_corrected_price_leaves_the_old_metric_value_readable` · `test_metric_transforms.py` |
| **A metric version is never overwritten.** A changed formula is a new version beside the old one, so an answer pointing at N still resolves. | `test_a_bumped_version_leaves_the_old_one_resolvable` · `test_metrics.py` |
| **Every field the project model holds is written back to the file.** Anything missing from `dump_project` is deleted by the next edit. | `test_every_field_the_model_holds_is_written_back` · `test_metrics.py` |
| **Nothing writes `qanat.yaml`.** It holds what a person writes; definitions, cadences, formulas and baskets live in `jobs.yaml`, `metrics.yaml` and `universes.yaml`, and saved filters in `filters.yaml`, which the tools own. | `test_no_authoring_verb_has_a_write_path_to_the_architecture_file` · `test_defining_a_metric_leaves_the_architecture_file_byte_identical` · `test_architecture_file.py` |
| **A universe is a csv, not a key.** A connector's `from_universe` names it; nothing in the project refers to one, and a screen takes its list with the call. | `test_a_universe_is_a_csv_and_nothing_in_the_project_names_one` · `test_the_surface.py` |
| **No tool declares an argument its handler ignores, or reads one it did not declare.** The second is a quiet alias, which is what `source` and `step` were retired for. | `test_no_tool_declares_an_argument_its_handler_ignores` · `test_the_surface.py` |
| **A screen takes `clauses` or a saved `filter`, never both.** Two at once has no obvious answer, so it is refused; a caller tweaking a saved filter sends its clauses changed. A clause is never a `{filter, clauses}` wrapper. `applied` echoes the version each clause read, which is the half the caller could not know. | `test_a_screen_takes_clauses_or_a_filter_and_never_both` · `test_applied_echoes_what_the_caller_could_not_have_known` · `test_the_surface.py` |
| **A filter is the operator's, and it is never a transform.** It lives in `filters.yaml`, writes no table and runs on no clock; a scope is not an identity, so nothing here is one user's. | `test_a_saved_filter_screens_the_same_as_its_clauses_inline` · `test_a_filter_writes_no_table_and_no_definition` · `test_filters.py` |
| **The store is Parquet files read by DuckDB, at both doors.** One shape everywhere — not a DuckDB file, not a server database. A reader is never blocked by a writer, and an append writes one new file. | `test_a_reader_is_not_blocked_by_a_writer` · `test_an_append_writes_one_new_file_and_leaves_the_rest` · `test_parquet_store.py` |
| **A program that serves takes the writer lock while it starts.** `meta.duckdb` is the lock; it is attached lazily so a reader never takes it, so a writer has to ask. Two servers used to both start and the second failed on its first write. | `test_a_second_program_is_refused_when_it_asks_for_the_lock` · `test_opening_a_store_does_not_take_the_writer_lock` · `test_parquet_store.py` |
| **A refusal names the holder, not the locked file.** `.qanat-lock` beside the store carries the pid, the address and the doors. Advisory throughout: the DuckDB lock is the authority, a stale note is replaced silently, and failing to write one is never a refusal. | `test_the_refusal_names_the_holder_and_not_only_the_file` · `test_a_stale_note_is_replaced_without_a_word` · `test_parquet_store.py` |
| **A reader never needs the writer lock to pick the current row.** The key registry is a plain file beside the store. In `meta.duckdb` it was unreadable without the lock, so `_current` skipped the pick and returned every version of a corrected row with nothing saying so. | `test_a_program_that_never_opens_the_metadata_file_still_picks_the_newest_row` · `test_parquet_store.py` |
| **A transform reads only what it declared.** The stage contract in `docs/contract.md` is deliberate; changing it needs discussion. | `test_a_step_cannot_read_what_it_did_not_declare` · `test_pipeline.py` |
| **A definition names no cadence and a schedule names no selection.** Either one carrying the other's fields is refused at load — that pairing is what made "Microsoft daily, Apple weekly" unanswerable. | `test_a_definition_carrying_a_cadence_is_refused_at_load` · `test_a_schedule_carrying_a_selection_is_refused` · `test_job_model.py` |
| **A retried job is one job and several executions.** Three attempts at the 07:00 poll are one `_qanat_job` row and three `_qanat_job_execution` rows, so "did the 07:00 poll succeed?" has an object to ask. | `test_a_definition_that_fails_twice_then_succeeds_is_one_job` · `test_job_model.py` |
| **`source` and `step` are retired, and refused rather than aliased.** `source` survives only as the per-row provenance column. | `test_the_word_step_is_gone_from_the_engine` · `test_the_word_source_survives_only_as_row_provenance` · `test_job_model.py` |
| **Every written row names the execution that wrote it**, and a job cannot set the column. It resolves up to the job, the definition and that definition's version. | `test_a_row_picked_at_random_names_the_whole_chain` · `test_a_job_cannot_write_the_column` · `test_provenance.py` |
| **A default read does not carry the engine's own columns.** A transform reading `SELECT *` would otherwise inherit its input's execution id and get a second one stamped beside it. | `test_a_default_read_does_not_carry_the_column` · `test_a_transform_and_its_input_name_different_executions` · `test_provenance.py` |
| **Several definitions may write one table, and they must agree on what identifies a row.** The old rule said one writer, because otherwise nothing could say which pass made the rows; every row names its execution now, so the rule went with the reason. | `test_a_different_selection_is_a_second_subscription` · `test_two_writers_must_agree_on_what_identifies_a_row` · `test_filings.py` |
| **A correction is a new row, and a read picks one.** A re-fetch with a changed value appends beside the stored row; the `qanat_now` view returns the newest write per key, and an as-of read filters by the clock *before* picking. | `test_a_re_fetch_with_a_changed_value_leaves_both_rows_readable` · `test_a_read_at_a_past_date_shows_what_was_knowable_then` · `test_never_replaced.py` |
| **An ingestion job may not replace what it already landed.** `mode: replace` rewrote the table to only what the poll returned, deleting every row landed outside the selection. | `test_a_source_declaring_mode_replace_is_refused_at_load` · `test_pipeline.py` |
| **The clock deletes nothing.** Retention is declared in the project and applied by `plan`'s `prune` flag, which lists the rows first. | `test_the_clock_deletes_nothing` · `test_mcp.py` |
| **No SEC request goes out without a contact.** Refused before the request, not after. | `test_a_missing_contact_is_refused_before_any_request` · `test_sec.py` |
| **A split is kept as an event, not only as an adjustment.** A split made every old price wrong once already — see 0.7.0. | `test_a_split_is_kept_as_an_event_not_only_as_an_adjustment` · `test_sec.py` |
| **The HTTP surface checks Host and token.** | `test_a_host_we_do_not_answer_to_is_refused` · `test_a_token_is_required_when_one_is_set` · `test_mcp_http.py` |
| **The app door holds no logic and no SQL.** A route calls one service function; a page needing an answer no function gives gets that function in the service layer first. It never calls `/mcp`, and `/mcp` never calls it. | `test_the_app_layer_holds_no_sql` · `test_app.py` |
| **An app write follows the scope like the tool doing the same write.** A server started at `research` serves pages that read and refuses the ones that write. Only the page shell passes without the token. | `test_a_write_below_its_tools_scope_is_refused` · `test_the_page_shell_passes_without_the_token_and_the_api_does_not` · `test_app.py` |

**A rule with no test does not belong in this table.** Counts, module lists and "currently uses X"
rot on the next commit; put them in `CONTRIBUTING.md` or nowhere.

## Two things that look like bugs and are not

**A row with no `as_of` does not filter at all.** The as-of views need a clock. Without one they pass
everything through silently, and `_check_lookahead` warns about exactly this. A fixture missing
`as_of` tests the absence of the clock rather than the clock — that was a real bug in
`test_reading.py`.

**`search_documents` uses a LEFT JOIN.** So a span whose document row is in the future survives the
join with a null entity. This is a property of the views, not of the query. An inner join would
fix it and would change what the tool returns when a span has no document row — **that is a
decision, not a cleanup.** Leave it until somebody decides.

## The changelog is part of the work

A release says what changed, and **a wrong claim gets its own release.** `0.6.1` exists only to
correct a size claim that `0.6.0` got wrong, and `0.8.0` shipped a new file layout without saying
so, which took a follow-up commit to admit. Bump the version, write the entry, and say the true
thing about what moved.
