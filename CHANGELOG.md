# Changelog

## 0.10.1 — 2026-10-31

### A fresh install could not write, because the floor moved

`uv tool install qanat-fdtl` resolves dependencies fresh and ignores `uv.lock`.
`duckdb>=1.0` had no upper bound, so an installed user got **duckdb 2.0.0** while
the lockfile pinned 1.5.5 and all three CI jobs ran `uv sync --frozen`. **The code
that shipped was never run against the DuckDB it shipped on.**

DuckDB 2.0 tightened the result lifecycle: a connection whose result was not
drained refuses `register()` and `unregister()`. `fetchone()` on a one-row result
never reaches end-of-stream, so it leaves a result open. Reads kept working — a
read is nothing but `execute`. **Writes are the only thing that registers a
frame, so every `store.write()` failed, on both store kinds, for every caller.**

```
ResourceInUseException: Resource In Use Error: connection has an unfinished
result from execute(); call complete() or abort(), or fetch until a fetch
returns no rows, before other work on this connection
```

**The worse half is not that writes failed.** On a parquet store the file was
written, the view was rebuilt, and *then* the exception came out of the cleanup
in a `finally` block — so the write landed and the caller was told it failed.
`qanat run` reported `0 rows, failed`; `qanat ls` reported the rows. Two commands
contradicted each other about one table, and re-running never cleared it.

`duckdb` is now capped at `>=1.0,<2`, which is the whole of this release: no code
changed, so anyone already stuck can `uv tool upgrade qanat-fdtl` and write
again. The undrained reads are a real defect in `store.py` and are fixed next,
along with the CI gap that made a major bump invisible — `--frozen` everywhere
cannot fail on a dependency it never resolves.

## 0.10.0 — 2026-10-31

### Six tools that take you from picking filings to a screened list

The surface was shaped for a different job than the product has. Walked end to
end on 0.9.0: it would let an agent rewire the pipeline and would not let it
define a metric — the one object the product is named after. The purpose is one
sentence, processing filings into metrics, and the pipeline that serves it is
the same in every project. So the pipeline stops being something a caller
assembles, and the surface becomes the two things somebody actually does:
**choose the filings, and define what to measure.**

#### Choosing the filings

`find_filings` asks EDGAR's submissions index what exists. **Any of the 106
forms**, because the index is uniform across all of them; no fetch and no store
write, because "what could I look at" is the question anybody opens with and it
should not cost a download. `ingest_filings` spends the fetch on the ones you
pick. `subscribe_filings` points the same selection forwards, on an interval and
at an hour you set, and `list_subscriptions` reports **whether anything is
actually running it** — which over stdio is nothing, because this server is your
client's child and a thread it started would die with the conversation. That is
a fact about process lifetime, and the docs said it was a rule about authority.

**What is not uniform is whether anything can be parsed out of a filing, and
EDGAR's own flag does not say.** `isInlineXBRL` is true for 29.8% of filings in
a 48,807-filing sample and means financial statements in 1.2% of those: for a
10-K or a 10-Q it is the same sentence, and nowhere else. An 8-K is 99.9%
flagged and carries 23 to 58 cover-page text tags and no numbers. `isXBRLNumeric`
is worse — it reads 4.3% on 10-Ks, which cannot be true. **Neither flag is read
anywhere in this release.** The question is answered twice instead, by two tests
that cost different amounts: a form allowlist in `find_filings`, and the
taxonomy of the facts actually parsed in `ingest_filings`. A filing holds
statements if it yields `us-gaap`, `ifrs-full`, `us-gaap-ebp` or `oef` facts;
`dei` and `ecd` alone are the cover page. That rule was correct on all 84 real
primary documents sampled across 26 forms.

**Only the 10-K and the 10-Q are ingested, and the boundary ships with them.**
The 106 forms sort into four piles by what they would need, and a product that
ingests one pile and crashes or silently empties on the rest is not scoped, it
is broken. So any form can be found and labelled, and a form outside the pile is
refused **with its pile named** — a 20-F is fetched, reports `ifrs-full`, and is
not ingested. A 6-K, S-1, FWP, SD or SC 13G used to raise `ET.ParseError` out of
`xbrl.parse`: inline XBRL is what forces a filing to be XHTML, so an untagged
form is loose HTML and strict parsing dies on it. Those now land zero rows and
say why.

**The 10-K/A is the case that bites.** It carries the form name of a 10-K and
the flag of a 10-K; three opened held two to four `dei` facts, a public float
and a share count. No allowlist can tell an amendment apart without opening it,
so `find_filings` labels one `unknown` rather than guessing in either direction.

#### Defining what to measure

`define_metric` and `remove_metric`. **Nothing wrote a metric before** — the
word `metric` appeared twice in `cli.py`, once in a help blurb and once inside
an example — so the product's one verb failed on a new project with
`no metric called pe. This project has: (none defined)`.

The verb is `define`, not `save`. `save` says *keep this for me*, and there is
no *me*: a scope is a capability boundary and not an identity one. **A formula
is run before it is stored**, against your project, through the same code path a
screen uses, so a syntax error or an unknown column comes back as the database's
own message instead of as a definition that fails on somebody else's call. The
one thing that is not a refusal is a table the project declares and has not
written yet, because defining a metric before the first run is the normal order
on a new project.

**Bumping the version is yours to decide, and it is refused rather than
guessed.** Redefining an id without naming a version used to be impossible and
is now an error: replacing version N changes what a kept answer meant, and
writing N+1 claims the meaning moved. Version N+1 is written beside N and both
still resolve.

A definition reports whether it was **shipped** with the product or **defined**
here. The example's `pe` and `net_margin` are labelled `origin: shipped` — a
starter set, enough to screen with on the day you install, and deliberately not
a metric library.

#### A filter is a named object, and it is yours

`screen` reported `"filter": null, "version": null` — two fields waiting for an
object that could not be created. It takes a named filter now:
`[{filter, version, clauses}]`, and the name comes back in `applied`, so
whatever keeps your filters can record which rule produced which list. **A
clause may pin `metric_version`**, and then somebody bumping a formula cannot
change what your rule computes.

**And a screen now returns enough to keep a record of it.** The rows carry
`values` — the numbers the verdict was reached on, for the companies that failed
as well as the ones that passed — and a new `execution` block names the
definition versions it actually read, the dates, and the corpus range it ran
over. A clause records `metric_version_used` alongside whatever it pinned, so an
unpinned clause can still say which definition computed it.

`execution.id` is a **digest of those inputs, not a random handle.** Two runs of
the same filter over the same definitions and the same corpus share an id; a
different id means an input moved. That matters because an as-of date protects a
replay from a filer restating their numbers and does **not** protect it from a
parser being fixed and the corpus reloaded — so a record that cannot tell those
apart is not a record. There is no wall-clock timestamp in it on purpose: the
same screen twice is the same answer, which is a claim this product makes and a
test holds, and when a caller asked is the caller's own fact to record.

#### A company index, and a screen can be bounded to one

`list_companies` is new, and **nothing answered the question before.**
`read_company` takes a filer and tells you about it; the only listing anywhere
was buried in the error you got for naming one it did not have. So a caller
building a list of companies to screen had no way to find out which names would
land — and a name that does not land is a company quietly missing from an answer.
It reports the filer, the ticker, the name, how many filings, which forms and the
dates they span, and `query` matches a ticker or a company name.

`screen` takes `universe`: **a list of tickers or CIKs, passed with the call.** A
ticker is placed against its filer out of that index, because the facts carry a
CIK and the thing a person writes in a list is a ticker.

**It is a list and not the name of a stored one.** An earlier cut of this also
accepted the name of a universe declared in the project file, and that was the
same mistake as storing a filter — it made this end look up a list belonging to
the caller. Which companies somebody cares about sits on the client's side of the
line next to their filters and their history, so it arrives with the call and
nothing here keeps it.

**It widens the answer as well as narrowing it, and that is the point.** Without
a universe the subjects are whoever the metrics returned, so there is no way to
know who is missing — and *"nobody else qualified"* is not the same answer as
*"I could not judge nine of them."* With one, a member that has no value for a
metric the filter names comes back as a row that could not be evaluated, and
`counts.dropped` says how many.

A name the project does not hold is **reported, never dropped**: a list of fifty
where ten are not held is a screen that quietly answered for forty. And the check
is against the company index rather than skipped — the first cut took any string
as a subject whenever the corpus carried no tickers, so a typo became a
"could not be judged" row indistinguishable from a real company with no data.

**Where there is no index, the answer says the names were not checked.** A
subject is not required to be a filer — the SEC corpus is the first dataset here,
not the subject — so a project that declares no documents table takes its names
as written, and `universe.checked` is `false`. **A name that was never checked is
not a name that passed a check**, and an empty index is not evidence that a name
is wrong.

`universe.matched` is **null** in that case rather than a count. It reported
`placed: 2` for a while, which reads as *"I found both of them"* when the truth
was *"I looked for neither"* — and `unknown: []` beside it looks exactly like a
clean result. A number that only means something under a condition has to be
absent when the condition fails, or the condition is the thing nobody reads.

Membership is **point-in-time** when entries carry `from` and `to`, reusing the
same rule a step's universe already followed. A list without them is reported as
such, because then it is today's membership applied to every date screened and
every number built on it is flattered by the names that survived.

The universe is part of `execution.id`: the same filter over a different list is
a different answer.

**Filters are not stored here, and that is the line this release draws.** This
is shared data infrastructure: it owns the filings, the facts, the metric
definitions and the screening execution. A filter is one caller's research
criteria rather than a financial dataset, and a server that cannot tell two
callers apart has nowhere to keep one correctly — stored here, one person's
threshold would be everybody's. Holding them would also mean taking on the
research workspace of every application that ever connects: portfolio screening,
factor research, thesis tracking. Your own agent or your own application keeps
them, and hands them back with the call.

**The obligation that falls on this end is the execution interface**, and it is
what makes the split workable rather than merely tidy: filters in, companies and
their metric values out, with enough metadata that the caller can store a run it
can read back later without asking again.

#### A screen refuses a window it cannot answer for

`coverage` reports the dates a screen can be answered for, per table and
overall. A read as of a date sees the rows published on or before it, so before
the oldest row **there is nothing to read** — and a screen there returns a list
of nobody, which is not the same answer as nobody passing. A window starting
before the corpus is now refused with the range it does hold, instead of coming
back quietly short. A date past the newest row is a note rather than a refusal:
it repeats the newest answer, which is flat and not false.

`counts` gains `nothing_evaluable`, said out loud rather than left to be
inferred from three zeros.

#### A company is somewhere to start

`read_company` takes a ticker or a CIK and returns the way in: which filings
this project holds, each one's sections with their lengths and **the rule that
found each boundary**, and which concepts are tagged. It returns a map and no
bodies, and its answer does not grow with the length of a filing — `read_section`
still pages the text and `read_facts` still returns the numbers.

Every other reading tool starts from a document, and the thing anybody asks
about is the company. The facts carry a CIK — `0000320193`, not `AAPL` — and a
screen returns those, so a screen's output can be handed straight back in.

#### The 10-Q gets its own vocabulary

`sections.py` held `FORM_10K` and applied it to whatever it was handed. The
10-K is fine: 21 to 23 spans on four filers. **The 10-Q was not** — 1 to 3 spans
of about ten Items, finding Item 1A and Item 4 and missing Item 1 and Item 2,
the statements and the MD&A. So a number from a 10-Q could not be traced to the
sentence it came from, which is a thing this product promises.

`FORM_10Q` ships. **A 10-Q numbers its Items inside Parts**, so the same number
is two different sections — Part I Item 1 is the financial statements and Part II
Item 1 is legal proceedings — and the Part is therefore part of the section
name. What makes that cheap is that the titles do not collide, so the title test
that already decides the number also decides the Part.

**`split` is now told which form it is working in, and a form without a
vocabulary returns no spans.** It used to default to the 10-K's, which is how
Microsoft's 8-K came back holding Item 1, Item 7 and Item 8 — those are `7.01`
and `8.01`, read through a pattern for a different form — and how Oracle's
DEF 14A produced an Item 7 from a heading that is not one. **A decimal Item
number is now declined by the regex outright**, rather than left to a title test
to reject. Zero spans is the honest answer and a small number is not: the
reading tools report no sections, which degrades cleanly.

No vocabulary ships for a form that is not ingested. One for a form nothing
reads would be a claim with nothing behind it.

#### The command line is four verbs

`init`, `check`, `mcp`, `schedule`. **`ls`, `graph`, `plan`, `prune`, `run`,
`screen` and `serve` are gone**, and running one prints where it went:

| gone | use instead |
| --- | --- |
| `ls` | `list_tables`, `list_steps`, `describe_table` — it was those three in one dump |
| `graph` | **nothing.** It drew a picture for a person out of a thing whose caller is a program, and its own markers disagreed with its legend: `○ 4 rows` for a table `ls` drew as `●` |
| `plan` | the `plan` tool |
| `prune` | the `plan` tool's `prune` flag |
| `run` | the `run` tool |
| `screen` | the `screen` tool |
| `serve` | `qanat mcp --http --schedule`, or the two halves separately |

Each was a second door onto a question a tool already answers, and a second door
is a second place the behaviour can quietly differ. `prune` was folded into
`plan` rather than kept: it was the one irreversible verb at the top level, and
folding it in means you see the list of orphans from the same call that drops
them.

What stays, stays because there is nothing to ask. `init` runs before a project
exists. `mcp` is the transport. `check` is deliberate — somebody connecting for
the first time has to be able to tell a broken project from a broken server, and
a tool cannot answer that when the tool is what is broken.

**The clock and the server are two commands now.** They were one process with
`--no-schedule` to turn the clock off and no way at all to run the clock alone,
which is backwards for both deployments that exist: a recorder wants a clock and
no open port, and a hosted reader wants a server over a store something else
fills. `qanat schedule` is the clock. `qanat mcp --http` is the server.
`qanat mcp --http --schedule` is one machine doing both, which is what the
container runs.

#### A fourth scope, and the default stops below it

`save_step`, `remove_step` and `save_source` moved to a new `authoring` scope
above the default. They keep working, and the caller they serve is the
professional bringing a feed in through the HTTP, SQL or CSV connector — but the
pipeline from a filing to a metric is the same in every project, so nobody
screening filings needs the tools that rewire it, and carrying them means an
agent can rewire it by accident. `define_metric` and `remove_metric` sit there
too: a formula is the same number for every reader of a given Qanat, so there is
no private definition and the owner of one is whoever runs the deployment.

`lineage` and `stale_tables` stay in `data`, unmoved. Both only mean something
to a caller who may change the pipeline, so both look like they belong above the
default as well — but each scope is a published contract, and taking a tool out
of `data` breaks what somebody has already built against it worse than adding
one would. Moving them is a separate decision, not a cleanup.

### Fixed: an edit deleted the metrics, the returns table and the corpus

`dump_project` did not write `metrics`, `returns` or `corpus` back. The
round-tripper deletes any key the new dict does not carry, so **one
`save_step`, `save_source` or `save_universe` call on a filings project removed
every metric definition, the price table a screen reads, and the three tables
the reading tools read** — and the dump then matched the file it had just
emptied, so the check that is supposed to refuse a bad write passed.

The function already carried a comment saying anything the model holds has to be
written back. Three fields were missing from the dict under it.
`test_every_field_the_model_holds_is_written_back` walks the model rather than a
list, so the next field added to one and not the other fails a test.

### Fixed: a parquet store lost its run log, its event log and its applied state

On every close. Found while taking the measurement for a subscription's last
run, which reported `None` on a pass that had just landed four rows.

A parquet store's own connection is `:memory:` and its search path is
`memory.main` then `meta.main` — data first, because a bare table name means a
data table. So `CREATE TABLE IF NOT EXISTS _qanat_runs` resolved to the front of
that path and built the three metadata tables **in memory**, in a file-backed
store. `meta.duckdb` was created, attached, and left empty.

What it cost, on a parquet store only:

- `list_runs` came back empty after a restart
- a subscription's last run read as never having happened
- the event log was gone, so nothing could say the project had changed underneath
- **`plan` found no applied state, so every job looked like it had never run** and
  every pass reported the whole project as new

The duckdb-file store was always right, which is why nothing caught it: the
tables land in the file there, and the suite writes and reads inside one process
on both store kinds. The equivalence claim — one shape at both doors — did not
hold here, and now a test asserts it directly on each kind.

Reading was never the problem. The bare names in every query fall through the
search path once nothing shadows them in memory; only the creation had to move.
Every metadata query now carries its database with it rather than trusting a
search path, because the reader connection pins its own and is cached per thread.

### Fixed: `list_subscriptions` reported no rows for a pass that landed rows

It read `rows` and the runs table calls the column `rows_out`, so every pass
came back `None` — in the one tool whose whole job is saying honestly what has
and has not happened.

### Fixed: `check` warned that nothing read the facts table

The never-read warning counted only steps as readers. A metric is a query rather
than a step, and the reading tools are readers in their own right — so **the
terminal table of every correct filings pipeline was reported as read by
nothing**, which is the one thing a metric is for. A metric's SQL and the three
tables named in `corpus:` now count. `examples/sec-10k` validates with no
warnings, and the paragraph in its README apologising for the message is gone.

### Removed: the contract rules for two stage kinds that left in 0.5.0

`weights` and `pnl` stopped being things the platform owned in 0.5.0 and their
rules stayed behind: at most one `weights` stage, a `pnl` stage needs one to
price, one table per step, no step reads another's weights, a source may not
write into a weights stage. A stage kind is a name you choose now, so a rule
keyed on one of five fixed words could only ever be enforced against a project
that happened to use them. `docs/contract.md` has said "four properties and no
names" for some time; `project.py` now agrees with it.

That is also what made `check` warn on every correct pipeline: the never-read
warning exempted exactly one stage kind, and it was `weights`.

`lineage`'s own description said *"which weights tables break if it breaks"*.

### Other

- The `sec` connector reads a universe: `from_universe: sp5` instead of an
  inline array, so a list of companies is a named list that outlives the source.
- The `sec` connector takes **several forms** — `form: [10-K, 10-Q]` — because a
  table has exactly one producer, so one form per source would have meant one
  form per corpus. `years` is per form.
- A source may bound the period it accepts: `from:` and `to:`.
- `screen` with no `filters` argument now errors naming the missing field. The
  schema said `required` and the handler did not check, so the call came back
  `subjects: 0, passed: 0, applied: []` — which reads as "nobody passed" rather
  than "you called it wrong".
- The CLI flag is `--filters`, matching the tool argument. One service layer had
  two spellings for one list of one kind of object. (The command itself is gone;
  the flag was renamed on the way out.)
- `store.time_range()` reports a table's oldest and newest timestamps, which is
  what `coverage` is computed from.
- `project.metric(id, version)` resolves a definition at a version, and
  `metric_versions(id)` lists them. Two definitions at one version is a contract
  error.

## 0.9.0 — 2026-10-26

### The reading tools ask for the rows they want

Every tool in the reading surface shared one helper, and the helper had no
filter in it:

```python
def _read(store, ref):
    return store.read(ref)        # SELECT * FROM the table
```

So opening one section of one filing read every section of every filing first,
and `read_facts` pulled all 43,104 facts in the example corpus to return fifty
of them. Each tool then filtered in pandas, after every row had already crossed
into Python.

Measured on the example corpus, counting rows where DuckDB hands them over:

| tool | rows before | rows after | before | after |
| --- | --- | --- | --- | --- |
| `list_sections` | 904 | **22** | 10.4 ms | **0.7 ms** |
| `read_section` | 904 | **1** | 10.5 ms | **6.4 ms** |
| `search_documents` | 944 | **8** | 14.8 ms | 17.1 ms |
| `read_facts` | 43,104 | **939** | 19.2 ms | **2.5 ms** |
| `check_number` | 43,104 | **939** | 19.6 ms | **3.0 ms** |

**The example corpus is forty filings, which is why nobody noticed.** The cost
is linear in the corpus. On the same text replicated to 31,500 filings —
714,160 spans — `search_documents` goes from **23,471 ms to 36 ms** and
`list_sections` from **55,774 ms to 162 ms**.

`search_documents` costs 2.3 ms more at forty filings and 650× less at 31,500.
The pandas loop was fast only because it stopped after the first eight matches,
having already pulled every row's text into memory to do it.

**`read_section` now takes its page with `substr` in the database**, so a 100 KB
Item 1A is not carried into Python to have 4 KB taken off the front of it. And
`list_sections` no longer asks for `text` at all — it reports lengths, and on a
filings corpus `text` is nearly every byte in the table.

**This is not a storage problem, and that is worth saying because it was nearly
answered by changing the store.** One Parquet file holds 6 row groups and ten
files hold 10, each carrying its own min and max per column, and DuckDB skips a
row group exactly as it skips a file — so the file layout moves this by a tenth
of a millisecond. The directory bought the write cost in 0.8.0. The format was
already chunked for the read.

### `store.rows()`, and `query()` binds its parameters

```python
store.rows(ref, where="document = ?", params=[doc],
           columns="section, char_start, rule", order_by="char_start")
```

`read()` is still there and still returns the whole table, which is right when
that is what you want. `rows()` is the one to reach for when you already know
which rows you are after.

`query()` takes `params` now, and both bind rather than interpolate. **A
document id is caller input and the caller is an agent**, so an id carrying a
quote has to stay an id.

`_readable()` is public as `readable()`, for a caller writing its own join that
still has to respect the as-of clock.

### Postgres is no longer a storage backend

`store:` takes a directory for parquet or a path for a DuckDB file. A
`postgresql://` DSN is not a store any more.

The corpus is one writer, append-only, never updated in place, and read in wide
scans, which is what parquet is for — so the server bought nothing the files do
not, and cost a second code path through every write, every as-of view and every
migration. What went with it: the `ATTACH` branch, a cast in `as_instant`, a
service container in two CI workflows, a compose service, 145 lines of tests,
and `_unshadow`, which existed only because Postgres refuses to drop a table a
view depends on.

**The `sql` connector stays.** Reading *from* a Postgres server as a data source
is a different thing from storing in one.

### The backtest ledger is gone

`backtest` and `replay` left the engine in 0.5.0. Three metadata tables did not:
`_qanat_backtests`, `_qanat_bt_weights` and `_qanat_bt_periods` were created on
every store open, with four migrations still being applied to them, and twelve
methods wrote and read them with no caller left. **So a weights table existed in
every store's metadata, which is the one thing this engine claims it has
nowhere.**

Two live callers pointed at that ledger and both were broken:

- **`list_steps(net=true)`** promised an agent what each step had earned across
  every screen it had had. It read the ledger. It had always returned nothing.
- **`save_step` offered `rebalance` and `decay`**, with a comment saying both
  had always been honoured. `Step` carries neither field and forbids extras, so
  an agent using either one wrote a project file that would no longer load.

`progress.py` went too — 128 lines whose own docstring said nothing read it and
that `run_backtest` filled it, and `run_backtest` had already gone.

The session and ask tables went with them. `_qanat_sessions` and `_qanat_asks`
had no callers either, the other half of the chat console removed across 0.4.0
and 0.5.0. **The metadata file is now runs, events and applied state.**

`store.py` is 1,280 lines, from 1,698.

### Breaking

**A `postgresql://` DSN in `store:` no longer opens.** A project on one has to
move its tables to a directory or a `.duckdb` file. There is no migration path
in the tool, because the tool can no longer read the old one — the rows come out
with `pg_dump` or a `COPY` from the server, and go in as parquet.

**`qanat init --postgres` is removed.** `--store` still takes any path.

**`list_steps` no longer accepts `net`**, and `save_step` no longer accepts
`rebalance` or `decay`. All three read or wrote the removed ledger; the two on
`save_step` could not have worked at all.

**`search_documents` now has a defined sort order**, `document` then
`char_start`. It had none before: a pandas left merge keeps the left side's
order, so the answer was whatever order the spans table happened to hold. On the
example corpus 75 of 77 checked responses are byte-identical and two differ in
order only. An agent asking twice should get the same answer twice, and the
accidental order would have moved on its own the first time a compaction merged
a day's files.

**Six store methods are gone**, none of them called from anywhere in the package:
`start_backtest`, `end_backtest`, `save_bt_weights`, `save_bt_period`,
`bt_periods`, `last_live_backtest` — and with them `backtests`, `backtest`,
`bt_weights`, `steps_net`, `session_backtests`, `step_sessions`, `bar_history`,
and the seven session and ask methods.

**An existing store keeps three orphan metadata tables.** Nothing reads them and
nothing breaks. Dropping them would mean adding migration code in order to
remove code.

## 0.8.0 — 2026-10-25

### An append writes what arrived, not the whole table again

A parquet table was one file, so every write rewrote all of it. Landing one
day of filings into a 100-million-row table wrote **892 MB to add 2 MB**. It
worked, and it threw away the thing parquet was chosen for.

A table is now a directory of files. An append writes one file and touches
nothing else:

```
tables/normalized__facts/
    0000.parquet
    0001.parquet      today, written once
```

Measured, appending to a table of each size:

| table | append | rewrite |
| --- | --- | --- |
| 1 M rows | 7 ms | 17 ms |
| 5 M rows | 8 ms | 53 ms |
| 20 M rows | **11 ms** | 199 ms |

The append stays flat while the rewrite grows with the table, which is the
whole point: the cost of landing a day should depend on the day, not on how
much history is behind it.

**A keyed append asks the footers before it decides.** Every parquet file
records the range it holds for each column, so a key that cannot already be
there needs no rewrite — which is every ordinary time-series append, because
the new dates come after the old ones. When a key really could be there, the
table is rewritten and the old row is replaced rather than duplicated.

**An existing project migrates itself.** A table written by 0.6 or 0.7 as one
file is moved into its own directory when the store opens.

### Breaking: the file layout, for anything reading the files directly

```
0.7   tables/raw__prices.parquet
0.8   tables/raw__prices/0000.parquet
```

Qanat migrates its own projects on open, so nothing inside the tool notices.
A script that reads the corpus without Qanat does: `glob('tables/*.parquet')`
now finds nothing.

**This counts as breaking because the files are a published interface, not an
implementation detail.** The tables are meant to be read without this engine —
standard format, documented schema — so the path they sit at is part of what
was promised. A reader wants `tables/<stage>__<table>/*.parquet`, and a table
is now every file in that directory rather than one file.

That is also the reason for the minor bump rather than a patch.

### Prices ask only for the day they are missing

The connector had a fixed `from:` date, so every poll re-downloaded the whole
history for every company. At eight companies that is 11,672 rows a run. At a
few thousand it is tens of millions, and Yahoo would stop answering.

The runner now reads the newest day already stored and hands it to the
connector, which resumes from there. In `examples/sec-10k` a second run
fetches **16 rows instead of 11,672**.

It resumes one day earlier than the newest stored row, not from the row
itself: the last poll may have run before the close and stored a partial bar.
Re-fetching one day costs one row and the key replaces it.

Only adapters that can use it are given it. `csv` has no upstream to ask.

### Fixed

**An append reported the size of the table rather than what it wrote.** An
incremental fetch of 16 rows logged `11,672 rows`, which looked exactly like
the full re-download it had just stopped doing.

**Appending to a table that did not exist yet crashed.** The branch that
creates the table also makes it exist, so a second check of that ran the
append path before any columns had been worked out. It failed on the first
cold build of the example and passed on every run after, which is the worst
way for a bug to behave.

**The column check now runs before the write path is chosen.** It had moved
inside one of the two paths, so a frame with the wrong columns took the cheap
path and landed as a file that `union_by_name` padded with nulls — a wrong
answer and no error, which is the failure the check exists to stop.


## 0.7.0 — 2026-10-24

### A split made every old price wrong, and the PE with it

Yahoo rewrites its price history after a stock split, and `auto_adjust=False`
does not turn that off — it turns off the *dividend* half. So both columns it
returns are split-adjusted, and the one named `close` is not the price that
traded. NVIDIA split 4-for-1 in 2021 and 10-for-1 in 2024, so its 2021 close
came back as **$13.11 against the $524.54 that was really on the screen**.

**A filing is never rewritten.** The SEC still reports NVIDIA's FY2022 diluted
EPS as $3.85. Dividing a restated price by an original EPS mixes two footings,
and the answer is wrong by the split factor with nothing to show for it:

| filed | `pe` before | `pe` now |
| --- | --- | --- |
| NVDA 2022-03-18 | 6.9 | **68.7** |
| NVDA 2023-02-24 | 13.4 | **133.8** |
| GOOGL 2022-02-02 | 1.3 | **26.4** |
| WMT 2022-03-18 | 10.0 | **29.9** |

Six of the forty filings in `examples/sec-10k` were affected. The error points
one way — a split makes an old price smaller, so it makes an old PE smaller —
so a value screen was pulled towards whatever had split. Alphabet read as a
PE of 1.3, which would have ranked it the cheapest company on the list.

### What changed

`raw.prices` keeps two more columns from the same request: `split_ratio`, the
split that took effect that day, and `dividend`. `history()` already carried
both, so this is a column kept rather than a second fetch.

`normalized.filing_prices` now carries three prices, and which to use depends
on the question:

| the question | the column |
| --- | --- |
| was it cheap? `pe`, `pbr` | **`close_as_printed`** — the other half of the ratio was never rewritten |
| what did it return? | **`adjusted_close`** — a split is not a loss |

`pe` moves to version 3 and reads `close_as_printed`. `returns:` moves to
`adjusted_close`, which also means a return now includes dividends.

### `adjusted_close` is stored, not recomputed

The opposite of the rule for splits, and the reason is measured. A split is
exact arithmetic, so the adjustment is recomputed from the event. Rebuilding
`adjusted_close` from the close and the dividends lands about **1.2% away**
from Yahoo's own figure, because vendors differ on the detail. A 1.2% error on
a yearly return is not acceptable, so the vendor's number is kept.

### `fetched_at`

`adjusted_close` keeps moving as later dividends are paid, so a stored value
with no fetch date cannot be audited. Every price row now records when it was
pulled.

### Breaking

`normalized.filing_prices` has new columns and `pe` is version 3, so a saved
answer pinned to `pe` v2 resolves to the old definition and the old number.
That is the versioning working. Re-run the pipeline to pick up the new one.


## 0.6.1 — 2026-10-24

### The size claim in 0.6.0 was wrong

It said the example corpus is 6.7 MB as parquet against **37 MB** in a DuckDB
file. The 37 MB was that file as it sat on disk after the example had been
rebuilt many times: about half of it was space freed by earlier writes that
the file had not given back. Comparing a fresh file to a fresh file, it is
**7 MB against 18 MB**.

Still worth having, and still mostly one table -- `normalized.spans` holds the
filing text and goes 12.6 MB to 5.4 MB, because prose repeats itself and
compresses hard. But two and a half times is the honest figure, not five and a
half, and the README said the wrong one on PyPI.

Documentation only. No code changed between 0.6.0 and 0.6.1.


## 0.6.0 — 2026-10-24

### The store is parquet files read by DuckDB

`store: ./data` — a directory — is the default for a new project. A `.duckdb`
path and a `postgresql://` DSN both still work and are unchanged.

**The reason is the second person.** A DuckDB file takes an exclusive lock, so
while one person is ingesting, a teammate opening the same project is refused
outright. Not slowed — refused. That limit is written down in three places in
this repo as a *hosting* limit, and it is not: it is a limit on the second
person, and a team sharing a corpus meets it on the first day. Open source is
ordinarily used by teams.

With parquet the writer builds a new file and renames it into place. A rename
within one directory is atomic, so a reader globbing for `*.parquet` sees the
old file or the new one and never a half-written one, and a reader already
part-way through a scan keeps the file it opened. Nobody waits.

Measured on `examples/sec-10k`, while a writer was landing 600,000 rows: the
reader answered in 1–3 ms. On a DuckDB file the same reader gets `StoreBusy`.
Both cases are tests.

### It is also smaller, by about two and a half times

The forty filings in `examples/sec-10k` are **7 MB as parquet against 18 MB in
a freshly built DuckDB file**, same rows.

Nearly all of the difference is one table. `normalized.spans` holds the filing
text -- 904 sections of English, some over 100 KB -- and goes 12.6 MB to 5.4 MB.
Prose repeats itself, so it compresses hard. `facts` is 4.7 MB to 1.4 MB and
the small tables barely move, because an empty DuckDB file is half a megabyte
before anything is in it.

The two formats are packed for different jobs. DuckDB keeps its pieces small
enough to reach one row, which is what updating and deleting need. Parquet
keeps them big, which compresses better and suits a table that is written once
and scanned. A filing is never edited, so nothing is given up here.

**The figure first published for this was 37 MB, which was wrong.** That was
the DuckDB file as it actually sat on disk after the example had been rebuilt
many times, and about half of it was space freed by earlier writes that the
file had not given back. The honest comparison is against a fresh file.

### What the tables look like

One file per table, under `<store>/tables/`:

```
data/
  tables/
    raw__documents.parquet
    normalized__facts.parquet
  meta.duckdb          the run log, opened only when something asks for it
```

**The metadata is the half that still wants a database**, because runs, events
and applied state are updated row at a time. It stays DuckDB, in its own file,
and is attached lazily — a caller that only reads data never opens it. That is
what keeps a teammate's query working while an ingest holds the log.

The cost is named: a pure reader gets no last-written timestamp on
`table_info`, because fetching it would mean taking the one lock the design
exists to avoid.

### Equivalence is the claim, so CI runs the suite twice

`QANAT_TEST_STORE=parquet uv run pytest` runs the whole suite against the new
backend. Same tests, same assertions. CI runs both on every push, because a
storage kind that passes its own tests and quietly fails the real ones is worse
than not having one.

### Fixed on the way

**Opening the run log lifted the as-of views.** Attaching the metadata file
reset `search_path`, which took the point-in-time schema off the front of it.
Every step then read the live table and the lookahead guard caught it as a
leak — the right failure for the wrong reason. One method now builds that
string and three callers use it.

**Retention reached into `store.con` with raw SQL.** It is `store.prune_rows()`
now, which a parquet store answers by rewriting the rows that stay.

### Breaking

Nothing. A project with `store: ./data/qanat.duckdb` behaves exactly as it did.
`qanat init` writes `store: ./data` for new projects only.

## 0.5.0 — 2026-10-19

### Real filings, and nothing invented left in the package

Every bundled example was made up, and three of the five did not load at all:
`equity`, `filings-sample` and `scheduled-ingest` still carried `split:`,
`benchmark:`, `bar:` and a double producer, which 0.4.0 made validation errors.
Nothing held them against `qanat check`, so nobody found out.

All five are gone, along with the `synthetic` connector that fed them. In their
place is one example built from documents the SEC published.

**`examples/sec-10k`** — eight companies, five years each, forty 10-K filings
downloaded from EDGAR on the first run. Apple, Microsoft, Alphabet, Meta,
NVIDIA, Walmart, Target and Costco: two groups that are each internally
comparable and not comparable with each other, which is what a relative screen
needs and what an honest one admits. 43,104 tagged numbers and 904 document
sections.

Their fiscal years end in January, February, June, August, September and
December, and the gap between period end and filing runs from 24 to 47 days. A
pipeline that only ever sees 31 December year-ends never has to get the clock
right. This one does.

### Three new pieces

**`sec` connector.** Resolves tickers, follows the paginated submissions pages
that a frequent filer's older 10-Ks hide behind, fetches each primary document
and stores it gzipped. The row it lands uses the platform's names — `id`,
`entity`, `doc_type`, `published_at`, `period_end`, `source` — with the SEC's
own keys in one `source_keys` field, so the core schema never learns what a CIK
is. Needs a contact in the User-Agent, which the SEC requires and which this
refuses to guess.

**`qanat.xbrl`.** Reads inline XBRL: resolves every context into a period and a
dimension set, resolves units including ratios, and applies `scale`, `sign` and
the transform a `format` names. Those three are the ones that matter — get any
wrong and the number is off by an order of magnitude or by its sign, and stays
plausible. Dimensions are kept as sorted JSON, because segment revenue and
total revenue are otherwise the same row. No new dependency: inline XBRL is
XHTML and the standard library parses it.

**`qanat.sections`.** Splits a filing into its Items and records how each
boundary was decided. Most matches for "Item 1A" in a 10-K are the contents
page or a cross-reference, not the heading, so the title is the signal and the
`rule` travels with the span.

### `qanat init` scaffolds against EDGAR

The starter pulled from the fake market and ran offline. It now pulls two
companies and two years of real 10-Ks, which needs a network and
`SEC_USER_AGENT` but no account and no API key. `--demo` runs the pipeline
instead of wiring a basket and pricing it.

The test suite grew a `conftest.py` that writes its own CSV-backed project, so
tests stay offline. A fixture may be invented where an example may not.

### Fixed

The package description 0.4.1 corrected in `pyproject.toml` was still in two
other places: `qanat --help` and `qanat/__init__.py` both announced an MCP
server that "tests each one inside its own industry", which is the claim 0.4.0
removed.

`examples/` is now held against `qanat check` by a test, which is how three
broken examples shipped.

### Screen is the only word, and it takes one date or a window

`qanat backtest` is gone, and so is the engine behind it. What replaces it is
`qanat screen` and a `screen` MCP tool, which answer the question the product
is named after.

```bash
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --as-of 2025-12-31
qanat screen --filter '[{"metric":"pe","op":"<","value":30}]' --from 2021-12-31 --to 2025-12-31
```

A window of one is a single date, so the rows come back the same shape either
way and a caller never branches on which form it asked for.

**Three outputs, and nothing else.** The list of who passed. Each company's
own forward return, from its filing to its next one. And the count of how many
could be evaluated at all, because a screen that quietly dropped half the
market has said something false about who passed.

Nothing is added up. No average across years, no comparison against the
companies that failed, no equity curve. The returns run between each company's
own dates, because Costco files in October and Walmart in March and there is
no shared calendar to price them on.

**`replay` is not a word any more, in the engine either.** It meant running
the graph once per as-of date, which is what screening over a window is. One
of the two had to go and the product is named after the other. 124 occurrences
across the package, including the marker written into the store — which is
read under both spellings so a store from 0.4.x still recovers correctly.

### Nothing simulates holding a position

Gone: turnover, fees, slippage, net, the equity curve, the hit rate, decay
blending, basket combining, the `pnl` stage and the `backtest:` block. Pricing
a selection as a portfolio needs a decision about how much of each company to
hold, and that decision is the user's and is made outside this package.

This is the second half of a cut 0.4.0 started. 0.4.0 removed the `weights`
MCP tool; the engine behind it stayed, and the README had to carry a paragraph
apologising for it.

### The layers are yours

`kind` stops being an enum of five. Only `raw` means anything to the platform:
a landed stage, written by sources and never by a step. Everything else is a
name you chose. `weights_stage`, `pnl_stage`, `weights_steps`, `weights_name`
and `weights_step` are gone from the model.

`docs/contract.md` is rewritten around the four properties that survive. The
cost is real and it is stated there: `check` can no longer catch a cleaning
layer placed after something computed, so a pipeline can be valid and still
compute nonsense.

### Metrics, and the line between a formula and a threshold

A `metrics:` block defines formulas with names and versions. A metric is
arithmetic — `pe = price / diluted EPS` — so it is shared and the same for
everybody. A threshold is a judgement, so it arrives with the call and never
lives in the project. A clause pairs the two.

Bump `version` rather than editing a formula in place: a saved answer points
at a version, so an edit silently changes what an old answer meant.

### The reading surface: five tools

`list_sections`, `read_section`, `search_documents`, `read_facts` and
`check_number`. Each has one rule behind it.

`read_section` pages and never returns a whole Item — Item 1A runs past 100 KB
in a large filer, and a tool that hands that over has spent the context the
agent needed to think with. `list_sections` reports each length first, and
which rule decided each boundary. `search_documents` returns locations and
never bodies. `check_number` puts a number you read in prose next to the
tagged numbers near it and **refuses to say which is right**, because a gap
means either the prose was misread or the two are different measures of the
same subject, and the tool can tell neither from the other.

Which tables these read is a `corpus:` block, because the platform does not
name layers.

### Prices, from Yahoo, with the licence recorded

A `yfinance` connector behind the `prices` extra. Both closes are kept and
which is which is a column: the printed close is what was true that day, and
the adjusted one is restated for every later split.

**It is not a source anything may be sold from.** Yahoo's terms do not grant
redistribution, so every row carries `source: yfinance` and that column is
what a later cleanup acts on. A store that mixes these in with licensed rows
and does not record which is which cannot be separated afterwards.

### The MCP surface

23 tools at `full`, 15 at `research`, 12 at `data`. Was 21 / 13 / 6.

Nothing in `research` writes anything at all now. A screen is a query, and
there is nowhere in Qanat a per-user result could correctly live, because the
store cannot tell two callers apart.

### Breaking

`qanat backtest`, `qanat backtests` and `qanat report` are gone; use `qanat screen`.
The `backtest`, `backtest_conditions`, `list_backtests`, `report` and `period` MCP tools go with them.
`backtest:` in a project file is rejected, as are `rebalance:` and `decay:` on a step.
`connector: synthetic` is gone; use `csv` with a file you wrote, or `sec`.
`qanat.sort` and `qanat.backtest` are gone, and so is `qanat init --demo`'s basket wiring.
`scaffold.add_demo_steps`, `DEMO_INDICATOR` and `DEMO_SORT` are gone.
Every bundled example is gone, replaced by `examples/sec-10k`.

### Not in this release

The metric map. `pe` and `net_margin` in the example are a worked example of
the problem, not a solution to it: filers do not agree on a tag, and NVIDIA
uses `Revenues` where Apple uses
`RevenueFromContractWithCustomerExcludingAssessedTax`. Taking one and not the
other produced a 271% net margin before it was caught. Mapping 50 to 100 plain
names onto the tags filers really use is the large piece of work left on the
fundamental side.

A price source anything may be sold from. `yfinance` unblocks the build and is
not that.

## 0.4.1 — 2026-10-18

### The package description said the thing 0.4.0 removed

PyPI and every package index read the one-liner out of `pyproject.toml`, and that line still said
*"an MCP server that turns filings and prices into valuation indicators, and tests each one inside
its own industry."* Testing an indicator inside its industry is exactly what 0.4.0 took out, so the
release shipped with a tagline contradicting it.

It now reads: **a data platform for investment research. It ingests financial data sources and
computes point-in-time financial metrics for valuation and screening. Delivered as an MCP server.**

Keywords follow: `backtesting`, `indicators` and `feature-engineering` out; `investment-research`,
`stock-screener`, `point-in-time`, `financial-data` and `data-platform` in.

Metadata only. No code changed between 0.4.0 and 0.4.1.


## 0.4.0 — 2026-10-18

### Qanat is a data platform for investment research, not a backtesting engine

> A data platform for investment research. It ingests financial data sources and computes
> point-in-time financial metrics for valuation and screening.

A data platform does four things: ingest, save, transform, serve. Plus an orchestrator that runs
those four and checks each one did what it should. This release removes everything in the package
that was not one of them. **2,120 lines out, 104 in.**

### Nothing in it judges a result any more

Gone: the significance bar (`bar:`, `read_bar`, `set_bar`), the trial ledger (`record_trial`,
`list_trials`), the indicator IC block (`--indicator`, `indicators:`, `ic`, `spread`, `autocorr`,
`ic_mean`, `ic_t`, `ic_hit`), run comparison (`qanat compare`, `compare`), `vol`, `sharpe` and the
t-statistic.

The reason is arithmetic rather than taste. An annual rebalance's sample size is the number of
years, not the number of companies, because every company in one year shares that year. At fourteen
annual rebalances, which is every year of XBRL filings there is, a significance test needs an IC of
0.069 and a real value indicator is 0.02 to 0.06. **The test fails on a true effect at any history
length anyone can have**, so printing it was worse than printing nothing.

What replaces it: a replay reports one row per rebalance date, and `periods` sits next to the
number. A figure from four rebalances and a figure from four hundred are not the same claim.

### Nothing in it simulates holding a position

Gone: the `weights` MCP tool, `benchmark:` and `benchmark_returns`, `benchmark_net`, `excess_net`,
and the in-sample / out-of-sample split (`split:`, `segment`).

A benchmark comparison and an excess return are statements about a portfolio, and a portfolio needs
a decision about how much of each company to hold. That decision is the user's and is made outside
this package.

### Live scoring is gone

Gone: `live:`, `live_steps:`, `live_from:`, the frontier that separated live from out-of-sample,
`live_window`, and the scheduler's forward-scoring pass. Live left the scope in 2026-09-04 and the
code had not caught up.

### The unattended pass is gone

Gone: `research.py` and `headless.py`, the `research:` and `agent:` blocks, and the scheduler hooks
that fired them. That was a loop driving an installed agent CLI overnight to try to break a strategy
and record every attempt, which is factor validation with a robot doing it.

**This one is a real loss and worth naming.** It was the only part of the package that worked while
nobody was watching, and the trial counter was the only defence against trying fifty indicators and
reporting the best. Neither is replaced. The product no longer makes the claim they protected.

### What stayed, and why

`scheduler.py` and `retention.py` were both on the cut list and both came back. The scheduler is the
orchestrator: ingestion is scheduled too, not only transformation, and a platform that only moves
when a person asks is not a platform. Row retention is part of saving.

The point-in-time replay stayed and is the most valuable code here. Each date reads only the rows
that were public then.

### The MCP surface is smaller

21 tools at `full`, 13 at `research`, 6 at `data`. Was 27 / 18 / 6. Every tool definition is sent on
every request, so this is a real cost saving per call as well as a shorter list for a model to pick
from.

### Breaking

Any project with `bar:`, `benchmark:`, `split:`, `indicators:`, `groups:`, `live:`, `live_steps:`,
`live_from:`, `research:` or `agent:` in its `qanat.yaml` will fail validation. Delete those keys.
Callers of the removed MCP tools or `qanat compare` will get an unknown-tool error.

### Not in this release

Three decisions are made and not built: user-named layers instead of the five fixed kinds, versioned
metric definitions with their values kept, and screening as one call rather than a step you write.
The README says which parts of it are the direction rather than the present.


## 0.3.2 — 2026-10-12

### An indicator can be scored on its own, not only through a basket

`qanat backtest --indicator features.momentum:momentum`, or `indicators:` in the `backtest:`
block, scores a column alongside the basket. Repeat the flag for several.

| | |
| --- | --- |
| `ic` | rank correlation between the column and the return that followed, per as-of date |
| `spread` | the top group's mean return minus the bottom group's, over `groups:` groups (default 5) |
| `autocorr` | rank correlation with the same column on the previous as-of date |

The summary carries `ic_mean`, `ic_t` and `ic_hit`, the share of dates the correlation was
positive. `qanat backtest` and `qanat report` print the block, the `backtest` MCP tool takes an
`indicators` array, and `report` returns both levels under `indicator_summary` and
`indicator_periods`.

**Nothing existing changes.** Two keys are added to a backtest result and two fields to
`BacktestResult`, named `indicator_periods` and `indicator_summary` the way `periods` and `totals`
already are. `_qanat_bt_periods` keeps its ten columns, `Period` keeps its ten fields, and a run
asked for no indicators returns an empty list for both.

Until now the only way to ask whether an indicator meant anything was to hold the top of it and
look at the money. That answers one cut of the ranking. A column can order the universe and still
lose in a basket of four names, and the reverse happens as often.

Both numbers are measured on the period's own two price days, so the fill rule does not change:
the return starts at the first price strictly after the as-of date. An indicator row only exists
for a period that was priced.

`ic` is `null` rather than `0.0` where fewer than three names could be compared, or where the
column or the returns hold one value. Where fewer than two names land in each end, no spread is
reported and the run says so once, naming the `groups:` value that would fit.

**One sweep is not one trial.** Scoring twenty columns is twenty attempts at finding something.
The engine does not record them for you: `record_trial` is still the caller's call, and
`bar: {rule: count}` is what divides the significance by how many there were.

New table `_qanat_bt_indicators`, one row per indicator per period, created on open like the rest
of the meta tables. Nothing existing changes shape.

### The description, and the page

`qanat --help`, the package summary and the README all said *an MCP server for quant factor
processing and backtesting*. They now say **an MCP server that turns filings and prices into
valuation indicators, and tests each one inside its own industry**. No code behaves differently.

The README was also a release behind: it offered four shelf strategies that 0.3.1 deleted, counted
31 tools where there are 27, and typed the `normalized` stage as `kind: features`, which `qanat
check` now refuses. `docs/agents.md` had the three scope counts wrong for the same reason.


## 0.3.1 — 2026-10-11

**Breaking, despite the patch number.** Four tools are deleted, two CLI flags are renamed,
one `qanat.yaml` field is renamed, and a database column is renamed on open. The upgrade
list is at the end of this section; read it before installing over a project you care about.

There are no deprecation aliases: registering old and new names doubles five tool
definitions, and every definition is sent on every request.

### `alpha` is deleted, and nothing replaced it

`docs/words.md` defined an alpha as *the step that writes a weights table*, which is
`step` plus a destination stage and no information of its own. A second word for one
thing is what the first line of that file forbids.

So the thing has no name, and the stage is what identifies it. `list_steps` gained a
`stage` filter, which is how you find what produces the weights tables now, and a `net`
flag that joins the replay history per step: `runs`, `last_run`, `last_net`, `best_net`,
and which step is wired. A step removed from the file keeps its history, because what it
earned still happened.

| Was | Is |
| --- | --- |
| `alpha_book` | `list_steps(stage: "weights", net: true)` |
| `list_alphas`, `read_alpha`, `use_alpha` | deleted with the shelf |
| `qanat alphas` | deleted |
| `--alpha` on `qanat backtest` | `--step` |
| `alpha` on the `backtest`, `list_trials` tools | `step` |
| `alpha` in the `bar:` block | `significance`, which is what it always meant |
| `live_alphas:` in `qanat.yaml` | `live_steps:` |
| `Project.alphas`, `.alpha()`, `.alpha_name()` | `.weights_steps`, `.weights_step()`, `.weights_name()` |
| the `alpha_` step-id prefix | dropped. The stage says it |

`list_steps` and `read_step` move from `full` to `research`. Reading a definition is not
authoring one, and the pair existed only because the shelf offered a research-scope view
of something else. **27 tools, down from 31.**

### The shelf is gone, and one sort replaced it

It held `momentum`, `reversal`, `low_vol` and `neutral_momentum`. Each computed one
number per symbol per date and then did the same three things: rank the universe, keep
the top few, weight them equally. A number per symbol per date is an **indicator**, and
it belongs in the features stage with every other computed column.

So there is no catalogue. `qanat init --demo` writes one indicator step and one sort over
it, and the variation lives in `options.column` rather than in a different script. That
is also the honest shape for what the engine does: testing whether an indicator meant
anything requires building a paper basket from it on each rebalance date, and the
procedure for doing that never changes.

### `normalized` is a stage kind

The scaffold always listed five stages and the enum had four, so the stage holding
point-in-time facts carried `kind: features` and the contract could not say where it
belonged. It can now: after `raw`, before anything computed, and `qanat check` enforces
both. Existing projects keep working, because a kind is a label — but a project written
before this release should change that one line.

### One database migration, and it is automatic

`_qanat_backtests.alpha` is renamed to `step` on open. Databases written before today are
renamed rather than re-created, so every backtest anyone already ran keeps its row.
Nothing else persisted changes, and no table is renamed.

The rename is **asked for rather than attempted**. A rename that fails because the column
is absent looks exactly like one that fails because the database is unwritable, and
swallowing the second to tolerate the first is how a migration goes quiet. So it reads
`information_schema` first. There is nothing to run: the first open of this version does it.

### Upgrading

| Was | Is |
| --- | --- |
| `alpha_book` | `list_steps(stage: "weights", net: true)` |
| `list_alphas`, `read_alpha`, `use_alpha` | deleted with the shelf |
| `qanat alphas` | deleted |
| `qanat backtest --alpha` | `--step` |
| `alpha` on the `backtest` and `list_trials` tools | `step` |
| `alpha:` in the `bar:` block | `significance:` |
| `live_alphas:` in `qanat.yaml` | `live_steps:` |
| `Project.alphas`, `.alpha()`, `.alpha_name()` | `.weights_steps`, `.weights_step()`, `.weights_name()` |
| the `alpha_` step-id prefix | drop it. The stage says it |
| `kind: features` on your `normalized` stage | `kind: normalized` |

180 tests, 6 skipped, and 13 audit batteries including the leakage one.

## 0.3.0 — 2026-09-30

### Qanat is an MCP server for quant factor processing and backtesting

Point it at any source that carries a timestamp. It processes that into factors, stores
them, replays the whole chain over history one date at a time with no lookahead, prices
what the portfolio held after fees and slippage, and serves all of it to your agent over
MCP.

That is what Qanat has always done. This is the release where the package says it and
says nothing else.

```bash
claude mcp add qanat -- qanat mcp --scope research
```

31 tools. Ask "what feeds the momentum strategy", "test this over 2015 to 2024 and tell
me what survived costs", "why did it lose money in March". Your agent reads the tables,
runs the replay, opens the period, and shows what was held.

### Three scopes, and each one is a promise

`--read-only` split the tools 23 and 10. That describes how the code is written and
promises nothing anybody could build against. It is replaced by three nested scopes:

    qanat mcp --scope data        6 tools   read the tables, including as-of
    qanat mcp --scope research   19 tools   adds replays, results, comparisons
    qanat mcp                    31 tools   adds authoring, ingest, scheduling

`data` is the door an institution connects at. `research` is the door a hosted service
connects at. Each is a published contract, so adding a tool to `data` changes what
somebody has already built against.

`scope` has no default on the tool decorator, so a tool cannot be added without naming
who may see it. And the split does real work: `set_bar` sits in `full` while
`record_trial` sits in `research`, so a caller can write down what it tried and cannot
lower the line those attempts are held to. A tool above your scope names its scope
instead of pretending not to exist.

### It no longer has to be on your machine

```bash
qanat mcp --http --port 8421 --scope research --token "$QANAT_MCP_TOKEN"
qanat serve     --port 8421 --scope research --token "$QANAT_MCP_TOKEN"   # and the scheduler
```

MCP's Streamable HTTP transport on `/mcp`, with sessions. The scope is fixed by the
command that starts the server, so nothing in a request can widen it. It binds to
127.0.0.1 and refuses any other address without a token.

`qanat serve` is that server with a clock: it opens the store, runs the scheduler, and
mounts the same endpoint, so one process holds the store and everything reaches the
project through it.

One process serves one project, because the store takes one writer. Two projects means
two processes, and many tenants means Postgres and a store each.

### What you install is the engine and the server

The console is gone, and so is the terminal app and the HTTP API behind them. Your agent
client is the screen.

That is about 14,000 lines out of the package, and it is the point rather than the cost.
A person who opened this repo used to see a chat window and a dashboard and decide what
Qanat was before reading a word. The answer to "agent? tool? engine?" was the code, not
the README. What installs now is the replay engine, the CLI that operates it, and the MCP
server, and nothing you have to look at.

`agent.py` is `headless.py`, because the filename was making the claim.

### The unattended pass got narrower and better fenced

It tries to break your best strategy overnight and records every attempt, and it used to
do that through a dozen curl endpoints with `--allowedTools Bash`. That is a wide door.
Asked once to reshape some ideas, the agent walked out of the project into Qanat's own
installed source and then into `~/.claude/projects`.

It now reaches the project over MCP at `--scope research`, with the tools that scope
offers and nothing besides. The scope is also what stops it editing the strategy it is
supposed to be attacking. That used to be a sentence in a prompt.

### Negative costs are refused everywhere, not in one place

A negative fee pays you to trade, so turnover becomes profit and the run lands in the
strategy book looking like a discovery: -9999 bps once put +1938% into it with nothing
marking it. That was refused by a `ge=0` on the console's request model, which meant it
held for exactly one caller. It is in the engine now.

### Upgrading

`--read-only` is gone: use `--scope data` to read tables, or `--scope research` to add
replays. `qanat serve` no longer opens a console and no longer serves `/api/*`; it serves
`/mcp`. `qanat tui` is gone. Everything else on the CLI is unchanged.

180 tests, and the release check that used to verify the wheel carried console assets now
verifies it carries none.

## 0.2.0 — 2026-09-21

### The conversation is the console

The Ask box was one line, repeated identically on four pages -- identical because it
had no relationship to any of them. A text field above an interface you drive
yourself is the wrong shape for a console whose agent can already reach every tool
it exposes.

The thread has a side now, and the space beside it stopped being a page and became
a response surface: ask about a table and the table opens, ask for a replay and the
equity curve is what you are looking at when the answer lands. The tab row is gone,
because it named the same four destinations the state spine already names.

Nothing new is reported to make that work. The agent talks to the console's own HTTP
API, so every tool call was already a request arriving at this server; a middleware
notes what was asked and which surface it is about. The page stamps its own fetches
so they stay out of it -- without that the console feeds itself, a repaint landing in
the trace that tells the console to repaint.

The reply streams. Server-sent events carry the text and the tool calls on one
connection, so their order is the order they happened in, and the client walks toward
the text a few characters a frame because a model emits a clause at a time.

### Four surfaces, and one of them was missing

**Data** profiles what you connected: fill, distinct count and the span each column
covers, computed in the database rather than by paging rows. The coverage bar is the
point -- it turns "57 rows" into "this reaches across a seventh of your history".

**Alpha** is the graph, and steps are wired on it by clicking the tables that feed
them. `from:` has always been a list and the scaffold's own `portfolio` alpha reads
three, but every editor offered one.

**Live** had an engine nobody could reach. It has a page: the forward return drawn
against the rate the out-of-sample half implied, the next pass date, and what it is
holding now.

### Fixed

- Live scoring never ran in a project with more than one alpha. The scheduler called
  `run_backtest` with no alpha, the refusal went to the event log, and the pass was
  retried every thirty seconds forever while the console said everything was fine.
  `live_alphas:` names the choice; a project that cannot say gets one error and stops.
- `live_from` was stamped before the run, and it writes once ever -- so a pass that
  then failed left the date defining out-of-sample pointing at a moment nothing was
  ever scored at. It is stamped after the pass lands.
- Periods that held nothing were counted as losses in the hit rate and as zeroes in
  the per-period average. `totals` now carries `held_periods` and `flat_periods`
  alongside both figures recomputed over the periods that held something.
- `POST /api/steps` passed its body straight to `Step`, which forbids extras, so
  `source` died in pydantic -- the console had never once created a step.
- Opening any alpha and pressing save wrote an empty options block over its
  `lookback` and `top_n`, because those fields were only drawn for a shelf rule.
- `save_project` was `yaml.safe_dump`, so the first edit anything made deleted every
  comment in `qanat.yaml`. Values are laid onto the tree parsed from the file now.
- The Ask agent wandered: asked to reshape some ideas it read qanat's own installed
  source and then `~/.claude/projects`. It runs with `Bash` alone and a brief that
  says where the project ends, and it no longer builds when asked not to.
- `use_alpha` refused on a project with no universe and said to edit `qanat.yaml` by
  hand, which an agent over MCP cannot do. `save_universe` is the tool that error
  now names.
- The four MCP write tools returned `{"files": [...]}` that never held files -- it
  was `rep.warnings` the whole time.
- The Results header on the backtest surface spilled over the strategies above it.
  Its rule ended in a comma and no block, so it ran on to the run log's rule and
  took `display:flex;flex:0 0 132px` -- which stacked the header onto three lines
  inside a bar 26px tall and un-hid the drag grip over the book.
- `qanat --version` and the banner `qanat serve` prints both said 0.1.3, because
  `__version__` was a literal that two releases bumped `pyproject.toml` without.
  It is read from the installed metadata now, so it cannot drift again.

### Added

- `profile_table` and `save_universe`, bringing the agent surface to 29 tools.
- `ruamel.yaml`, so an edit keeps the comments somebody wrote.

## 0.1.5 — 2026-09-15

### Ask it in plain English

The console has an input box on both pages, and it holds no key. It runs whichever
agent CLI is already installed and signed in -- Claude Code, Cursor -- headless, in
the project directory. Nothing to paste, nothing to bill, and the credential stays
where the person put it.

A DuckDB file takes one writer and the console is holding it, so the agent cannot
open a second `qanat mcp` in the same project. It is pointed at the console's own
HTTP API instead: the same service layer the MCP tools sit on, reached over the
loopback port already serving. One process, one writer, no lock.

What it did shows underneath as it happens -- the calls it made, then a `diff` of
what actually changed in the project, `weights.momentum · 4 → 5 rows`. The panel
floats rather than pushing the report down the page, folds away while it is still
working (the bar keeps a one-line ticker so nothing is lost), and `ask` becomes
`stop` while it runs. Stopping kills the process; whatever it already did stays done.

### Two pages, not one screen

Strategies answers "did my idea make money": the book as a table across the top,
then the report, equity curve first. Pipeline answers "how was this built": the
graph, the tables, the steps that wrote them. You only need the second when the
first gives you a number worth questioning, and the run log is on both because an
agent can be writing a step while you read a result.

The backtest form asks four questions -- alpha, from, to, rebalance -- with the
other eight under `costs and controls`. Nothing was removed; you are just not asked
about `embargo` before you have asked anything else.

### The console only answers to this machine

`POST /api/steps` writes a step script and `POST /api/jobs/<id>/run` runs it, so a
page that can reach the API can run code on the machine serving it. Binding to
loopback does not prevent that: a site you visit can resolve its own domain to
127.0.0.1 and call the console believing it is same-origin, and CORS never enters
into it. Every request is now checked against the name the browser asked for.
Anything but loopback is refused, and serving under a real hostname is a deliberate
setting: `QANAT_ALLOWED_HOSTS=qanat.example.com`.

### Who did it

Every event carries an actor -- `agent`, `you`, or `schedule` -- and the run log
shows it. A project that changes while you are reading something else should say
who changed it.

### Fixed

- The console's `api()` helper accepted an options argument and ignored it, so every
  caller that thought it was posting was quietly issuing a GET.
- The README led with the mechanism. It now says what the thing does first.

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
