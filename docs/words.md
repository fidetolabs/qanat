# The words

Each word means exactly one thing, and there is no second word for it.

| Word | What it is |
| --- | --- |
| **stage** | One step of the pipeline, holding tables. Order in `qanat.yaml` is order in the pipeline. |
| **table** | One dataset inside a stage. Always addressed `stage.table` — never as two fields. |
| **source** | Brings a table in from outside. |
| **connector** | *How* a source connects: `rest`, `sql`, `csv`, `synthetic`. |
| **step** | A script that turns tables into tables. |
| **universe** | The symbols a portfolio is allowed to hold. Point-in-time when its file carries `from` and `to`. |
| **alpha** | The step that writes a weights table. One alpha is one portfolio. |
| **lineage** | What an alpha reads, back to the source. Alphas share lineage; that is why they are comparable. |
| **alphaset** | What is priced together as one book: one alpha, or several. It has exactly one PnL table. |
| **job** | A source or a step — anything that runs. |
| **run** | One execution of a job. What the log and the history record. |

One word belongs to the console: a **slab** is one **table** drawn in the graph. The graph draws
tables as boxes and steps as the arrows between them — `table — (step) — table` — because that is
what a pipeline is.

Four more words belong to `qanat backtest`:

| Word | What it is |
| --- | --- |
| **replay** | Running the graph once per as-of date, over data that already landed. |
| **as-of** | The moment a pass is pretending to be. Nothing newer is readable, and nothing newer may be written. |
| **period** | The gap between one as-of date and the next. What was held across it, and what that returned. |
| **net** | What the money did: the periods compounded. The number a backtest reports as its answer. |
| **net sum** | The same periods added instead of compounded. Gross minus fees minus slippage reconciles against this one. |
| **decay** | Holding a blend of the last N portfolios, newest heaviest, to stop paying fees for noise. |

Three more words belong to `qanat plan`:

| Word | What it is |
| --- | --- |
| **drift** | The file and the database disagree. |
| **orphan** | A table still in the database that nothing in the file produces any more. |
| **stale** | A table whose rows were computed by a job that has changed since. Not deleted — just no longer current. |

A **stage** has a **kind**: `raw`, `features`, `weights` or `pnl`.

## The fields, one spelling each

| Field | On | Means |
| --- | --- | --- |
| `from:` | step | the tables it reads |
| `to:` | source, step | the tables it writes |
| `connector:` | source | rest · sql · csv · synthetic |
| `script:` | step | path to a `.sql` or `.py` file |
| `schedule:` | source, step | a cron line |
| `options:` | source, step | free configuration, passed through |
| `universe:` | step | which universe it may hold |
| `rebalance:` | step | how often this alpha wants to decide; a backtest uses it unless told otherwise |
| `decay:` | step | how many portfolios this alpha wants blended |
| `mode:` | source | `append` or `replace` |
| `kind:` | stage | `raw` · `features` · `weights` · `pnl` |
| `store:` | project | path to the one DuckDB file |
| `backtest:` | project | what prices the portfolio, and what holding it costs |

A source and a step both say `to:`, because writing a table is the same act whichever one does it.

## The one place a word has two spellings

In YAML a job says `from:` and `to:`. In Python those are `.reads` and `.writes`, because `from`
is a reserved keyword. Nowhere else does one thing have two names.
