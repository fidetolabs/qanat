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

`CONTRIBUTING.md` has setup, the architecture table and how to add a connector. Read it instead of
reading `src/` — it is two dozen modules and you need two of them.

## The rules that are not style

**Each one is held by a test. If you break the rule, the named test fails — that is how this file
stays true.** The table is the contract; the test is the enforcement.

| Rule | Held by |
| --- | --- |
| **No reading tool loads a whole table.** A tool asks for the rows it wants. Filtering in pandas after the rows cross into Python is the bug, not the style. | `test_no_reading_tool_loads_a_whole_table` · `test_reading.py` |
| **A read can never see past the as-of date**, filtered or not. This is the product's whole claim. | `test_a_filtered_read_still_cannot_see_past_the_as_of_date` · `test_reading.py` |
| **Every tool names one of three scopes, and the scopes nest.** A tool declared without one is refused at import. | `test_every_tool_names_one_of_the_three_scopes` · `test_a_tool_cannot_be_declared_without_a_scope` · `test_the_scopes_nest` · `test_mcp.py` |
| **The store is Parquet files read by DuckDB, at both doors.** One shape everywhere — not a DuckDB file, not a server database. A reader is never blocked by a writer, and an append writes one new file. | `test_a_reader_is_not_blocked_by_a_writer` · `test_an_append_writes_one_new_file_and_leaves_the_rest` · `test_parquet_store.py` |
| **A step reads only what it declared.** The stage contract in `docs/contract.md` is deliberate; changing it needs discussion. | `test_a_step_cannot_read_what_it_did_not_declare` · `test_pipeline.py` |
| **No SEC request goes out without a contact.** Refused before the request, not after. | `test_a_missing_contact_is_refused_before_any_request` · `test_sec.py` |
| **A split is kept as an event, not only as an adjustment.** A split made every old price wrong once already — see 0.7.0. | `test_a_split_is_kept_as_an_event_not_only_as_an_adjustment` · `test_sec.py` |
| **The HTTP surface checks Host and token.** | `test_a_host_we_do_not_answer_to_is_refused` · `test_a_token_is_required_when_one_is_set` · `test_mcp_http.py` |

**A rule with no test does not belong in this table.** Counts, module lists and "currently uses X"
rot on the next commit; put them in `CONTRIBUTING.md` or nowhere.

## Two things that look like bugs and are not

**A row with no `as_of` does not filter at all.** The as-of views need a clock. Without one they pass
everything through silently, and `_check_lookahead` warns about exactly this. A fixture missing
`as_of` tests the absence of the clock rather than the clock — that was a real bug in
`test_reading.py`, found in T-17.

**`search_documents` uses a LEFT JOIN.** So a span whose document row is in the future survives the
join with a null entity. This is a property of the views, not of the query. An inner join would
fix it and would change what the tool returns when a span has no document row — **that is a
decision, not a cleanup.** Leave it until somebody decides.

## The changelog is part of the work

A release says what changed, and **a wrong claim gets its own release.** `0.6.1` exists only to
correct a size claim that `0.6.0` got wrong, and `0.8.0` shipped a new file layout without saying
so, which took a follow-up commit to admit. Bump the version, write the entry, and say the true
thing about what moved.

## The ticket lives in fdtl-kb

Work here starts as a task card in `../fdtl-kb`, one target, one acceptance list:

```sh
cd ../fdtl-kb && bin/kb board            # what is open
cd ../fdtl-kb && bin/kb trace T-17       # the decisions and observations behind one task
```

**Closing a card needs a measurement in it**, not a claim that it works. T-17 is the example: rows and
milliseconds before and after, each acceptance box ticked, and a note on what the build turned up
and deliberately did not change.

The rules in the table above come from decisions in that record. If a decision is superseded, this
file is the thing to check — no checker crosses from there to here.
