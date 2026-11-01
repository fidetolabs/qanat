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
`migrate`, `mcp`, `schedule`. `ls`, `graph`, `plan`, `prune`, `run`, `screen` and
`serve` were removed in 0.10.0 -- each was a person's door onto a question a tool
already answers, which is a second place the behaviour can quietly differ.
Running one prints where it went. **`qanat mcp` serves; `qanat schedule` runs the
clock and serves nothing; `qanat mcp --http --schedule` is one machine doing
both.** **`qanat migrate` rewrites a project written in the old vocabulary** --
it runs on a file that cannot be loaded yet, so no tool could reach it.

`CONTRIBUTING.md` has setup, the architecture table and how to add a connector. Read it instead of
reading `src/` — it is two dozen modules and you need two of them.

## The rules that are not style

**Each one is held by a test. If you break the rule, the named test fails — that is how this file
stays true.** The table is the contract; the test is the enforcement.

| Rule | Held by |
| --- | --- |
| **No reading tool loads a whole table.** A tool asks for the rows it wants. Filtering in pandas after the rows cross into Python is the bug, not the style. | `test_no_reading_tool_loads_a_whole_table` · `test_reading.py` |
| **A read can never see past the as-of date**, filtered or not. This is the product's whole claim. | `test_a_filtered_read_still_cannot_see_past_the_as_of_date` · `test_reading.py` |
| **Every tool names one of four scopes, and the scopes nest.** A tool declared without one is refused at import. | `test_every_tool_names_one_of_the_scopes` · `test_a_tool_cannot_be_declared_without_a_scope` · `test_the_scopes_nest` · `test_mcp.py` |
| **The default scope is not the widest one.** Nothing that rewires the pipeline is offered to a caller who did not ask for `authoring`. | `test_the_default_scope_is_not_the_widest_one` · `test_mcp.py` |
| **A section vocabulary belongs to one form, and a form without one gets no spans.** `split` is told the form; it never falls through to the 10-K's. | `test_a_form_with_no_vocabulary_returns_an_empty_span_list` · `test_split_cannot_be_called_without_a_form` · `test_sections.py` |
| **A filing is ingestable because of the taxonomy its facts carry, never because of EDGAR's flag.** The flag is true for 29.8% of filings and means financial statements in 1.2%. | `test_no_flag_of_edgars_is_read_anywhere_behind_the_label` · `test_filings.py` |
| **A metric version is never overwritten.** A changed formula is a new version beside the old one, so an answer pointing at N still resolves. | `test_a_bumped_version_leaves_the_old_one_resolvable` · `test_metrics.py` |
| **Every field the project model holds is written back to the file.** Anything missing from `dump_project` is deleted by the next edit. | `test_every_field_the_model_holds_is_written_back` · `test_metrics.py` |
| **Nothing writes `qanat.yaml`.** It holds what a person writes; definitions, cadences, formulas and baskets live in `jobs.yaml`, `metrics.yaml` and `universes.yaml`, which the tools own. | `test_no_authoring_verb_has_a_write_path_to_the_architecture_file` · `test_defining_a_metric_leaves_the_architecture_file_byte_identical` · `test_architecture_file.py` |
| **No filter is stored here.** A filter is one caller's research criteria and a scope is not an identity, so it arrives with the call and leaves with the answer. | `test_nothing_on_this_surface_stores_a_filter` · `test_metrics.py` |
| **The store is Parquet files read by DuckDB, at both doors.** One shape everywhere — not a DuckDB file, not a server database. A reader is never blocked by a writer, and an append writes one new file. | `test_a_reader_is_not_blocked_by_a_writer` · `test_an_append_writes_one_new_file_and_leaves_the_rest` · `test_parquet_store.py` |
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
