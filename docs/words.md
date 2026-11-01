# The words

Each word means exactly one thing, and there is no second word for it.

| Word | What it is |
| --- | --- |
| **stage** | One layer of the pipeline, holding tables. Order in `qanat.yaml` is order in the pipeline. |
| **table** | One dataset inside a stage. Always addressed `stage.table` — never as two fields. |
| **connector** | *How* an ingestion job reaches outside: `sec`, `yfinance`, `rest`, `sql`, `csv`. |
| **universe** | The symbols a portfolio is allowed to hold. Point-in-time when its file carries `from` and `to`. |
| **lineage** | Where a value came from: the execution that wrote its row, the job that execution was an attempt at, the definition behind that job, and which version of it. |

**A job is four objects**, and the same four serve both kinds:

| Word | What it is | Does not hold |
| --- | --- | --- |
| **ingestion job definition** | What to fetch, through which connector, and the one table it lands in | any cadence |
| **transform job definition** | Which tables go in, which come out, and the script between them | any cadence |
| **job schedule** | One cron line, over one definition, on or off | any selection |
| **job** | One definition, for one exact datetime, and where that came from — a schedule, or nothing | anything reusable |
| **job execution** | One attempt: started, ended, status, rows, error, attempt number | the datetime it was for — that is the job's |

**A definition names no cadence and a schedule names no selection, and both are
refused for carrying the other's fields.** They were one object called a
`source`, with `schedule:` welded on as a field — so *what to fetch*, *how
often* and *which table* were one edit, and "Microsoft daily, Apple weekly" had
no answer. Several schedules may point at one definition; a definition with none
is ordinary and runs when asked.

**A retried job is one job and several executions.** Three attempts at the 07:00
poll used to be three unrelated rows, so *"did the 07:00 poll succeed?"* had no
object to ask.

**`source` and `step` are retired.** `source` meant four things — a declaration
in the file, a module in the package, the `connector:` value, and a string on
every row — and only the last was ever unambiguous, so it is the only one that
keeps the word. `step` named a position in a sequence and said nothing about a
table going in and a table coming out, which is the only thing one does. The old
spellings are **refused, not aliased**; `qanat migrate` rewrites a project that
still uses them.

One word belongs to the picture: a **slab** is one **table** drawn in the graph. The graph draws
tables as boxes and transforms as the arrows between them — `table — (transform) — table` —
because that is what a pipeline is.

Seven words belong to the screen:

| Word | What it is |
| --- | --- |
| **screen** | The verb, and the only one. Ask who passed a filter — on one date, or on every date in a window. A window of one is a single date, so there is one verb and not two. **There is no noun `screen`**: what you save is a *filter*, and what happened is a *run*. |
| **filter** | A named rule, holding one or more clauses. Versioned. "My usual set" is a filter. |
| **clause** | One condition inside a filter: a metric against a threshold. It has no name and no version of its own, and is never a table. |
| **metric** | A formula with a name and a version. Arithmetic, no judgement: `pe = price / diluted EPS`. Shared, because it is the same number for everybody. |
| **threshold** | The number a clause compares a metric against. A judgement, so it belongs to whoever set it and never to the project. |
| **as-of** | The date being asked about. Nothing published later is readable. |
| **run** | One application of filters, with what ran recorded as data rather than as a pointer. |

The split that matters is **formula against threshold**. A formula is not an
opinion, so it is shared and computed once. A threshold is an opinion, so it is
one person's. A clause is where the two meet.

**`replay` and `re-run` are not words here.** There was a `replay` for running
the graph once per as-of date, and it meant the same thing as screening over a
window. One of them had to go, and the product is named after the other.

Three more words belong to the `plan` tool:

| Word | What it is |
| --- | --- |
| **drift** | The file and the database disagree. |
| **orphan** | A table still in the database that nothing in the file produces any more. |
| **stale** | A table whose rows were computed by a definition that has changed since. Not deleted — just no longer current. |

A **stage** has an **id** you choose and a **kind**. The only kind that means anything to
the platform is `raw`: a landed stage, written by ingestion and never by a transform.
Everything else is a stage with a name you picked.

**The five fixed kinds are gone.** `raw`, `normalized`, `features`, `weights` and `pnl` were
an enum, and a project had to be shaped like the one product they were written for. A stage
now declares what is true of it rather than which slot it fills, and what `check` enforces is
in [contract.md](contract.md). `normalized` in the starter project is a name, not a keyword.

## The fields, one spelling each

| Field | On | Means |
| --- | --- | --- |
| `from:` | transform definition | the tables it reads |
| `to:` | either definition | the tables it writes |
| `connector:` | ingestion definition | sec · yfinance · rest · sql · csv |
| `key:` | ingestion definition | the columns that identify one row |
| `script:` | transform definition | path to a `.sql` or `.py` file |
| `options:` | either definition | free configuration, passed through |
| `universe:` | transform definition | which universe it may hold |
| `when:` | transform definition | tables whose new rows run it, instead of a clock |
| `mode:` | ingestion definition | `append`, and only that. `replace` is refused: it rewrites the table to what the last poll returned |
| `definition:` | job schedule | which definition this cadence fires, by id |
| `cron:` | job schedule | the cron line |
| `enabled:` | job schedule | off without being deleted |
| `kind:` | stage | `raw` for a landed stage; otherwise a name you choose |
| `time_columns:` | project | which column on a table says when a row became public. The one line the point-in-time reads depend on |
| `metrics:` | project | the formulas this project defines, each with a version |
| `returns:` | project | which table says what a company did next, so a screen can report it |
| `corpus:` | project | which tables hold documents, their text and their facts, for the reading tools |
| `store:` | project | where the tables live: a directory for parquet, or a `.duckdb` path for one file |

**Both definitions say `to:`**, because writing a table is the same act whichever
one does it. **Neither says `schedule:`** — that is a `job_schedules:` row, and a
definition carrying one is refused at load.

## The one place a word has two spellings

In YAML a job says `from:` and `to:`. In Python those are `.reads` and `.writes`, because `from`
is a reserved keyword. Nowhere else does one thing have two names.
