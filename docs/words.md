# The words

Each word means exactly one thing, and there is no second word for it.

| Word | What it is |
| --- | --- |
| **stage** | One step of the pipeline, holding tables. Order in `qanat.yaml` is order in the pipeline. |
| **table** | One dataset inside a stage. Always addressed `stage.table` — never as two fields. |
| **source** | Brings a table in from outside. |
| **connector** | *How* a source connects: `sec`, `yfinance`, `rest`, `sql`, `csv`. |
| **step** | A script that turns tables into tables. |
| **universe** | The symbols a portfolio is allowed to hold. Point-in-time when its file carries `from` and `to`. |
| **lineage** | What a step reads, back to the source. Steps share lineage; that is why they are comparable. |
| **job** | A source or a step — anything that runs. |
| **run** | One execution of a job. What the log and the history record. |

One word belongs to the picture: a **slab** is one **table** drawn in the graph. The graph draws
tables as boxes and steps as the arrows between them — `table — (step) — table` — because that is
what a pipeline is.

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

Three more words belong to `qanat plan`:

| Word | What it is |
| --- | --- |
| **drift** | The file and the database disagree. |
| **orphan** | A table still in the database that nothing in the file produces any more. |
| **stale** | A table whose rows were computed by a job that has changed since. Not deleted — just no longer current. |

A **stage** has an **id** you choose and a **kind**. The only kind that means anything to
the platform is `raw`: a landed stage, written by sources and never by a step. Everything
else is a stage with a name you picked.

**The five fixed kinds are gone.** `raw`, `normalized`, `features`, `weights` and `pnl` were
an enum, and a project had to be shaped like the one product they were written for. A stage
now declares what is true of it rather than which slot it fills, and what `check` enforces is
in [contract.md](contract.md). `normalized` in the starter project is a name, not a keyword.

## The fields, one spelling each

| Field | On | Means |
| --- | --- | --- |
| `from:` | step | the tables it reads |
| `to:` | source, step | the tables it writes |
| `connector:` | source | sec · yfinance · rest · sql · csv |
| `script:` | step | path to a `.sql` or `.py` file |
| `schedule:` | source, step | a cron line |
| `options:` | source, step | free configuration, passed through |
| `universe:` | step | which universe it may hold |
| `mode:` | source | `append` or `replace` |
| `kind:` | stage | `raw` for a landed stage; otherwise a name you choose |
| `time_columns:` | project | which column on a table says when a row became public. The one line the point-in-time reads depend on |
| `metrics:` | project | the formulas this project defines, each with a version |
| `returns:` | project | which table says what a company did next, so a screen can report it |
| `corpus:` | project | which tables hold documents, their text and their facts, for the reading tools |
| `store:` | project | path to the one DuckDB file |

A source and a step both say `to:`, because writing a table is the same act whichever one does it.

## The one place a word has two spellings

In YAML a job says `from:` and `to:`. In Python those are `.reads` and `.writes`, because `from`
is a reserved keyword. Nowhere else does one thing have two names.
