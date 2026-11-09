# The flows, and why each tool exists

**A tool is on this surface because a flow needs it.** Having an entity is not a
reason to have a tool over it, and that is the clause this file exists to hold:
the data model went from two objects to eight, and the surface did not move.

A mirror of the model would be about **thirty-two tools** — four objects, two
kinds, create-read-update-delete — generated from the shape of the schema rather
than from anything a caller needs.

**The two layers are named on different principles.**

| Layer | Named for | Example |
| --- | --- | --- |
| entities — tables, the project files, the model | **what the thing is** | `ingestion_job_schedule` |
| tools — this surface | **what the caller does** | `subscribe_filings` |

So no tool name contains `job`, `definition`, `schedule` or `execution`. A test
holds that.

**Both doors run the same flows, so a door is not a flow.** A door ① caller has
open-source Qanat and the agent client they already run — no screen, so the tool
surface is the whole product they get. A door ② caller reaches the same tools
through hosted Margiana. Neither door is a reason for a tool the other does not
need, and in particular *the open-source caller authors jobs by hand* is **not a
flow**: they pick filings and define metrics through these tools like everybody
else. It also raises what a miss costs — a flow with no tool leaves door ① with
no way to do the thing at all, where door ② would still have a screen around it.

---

## Getting data in

| The caller wants to | Tool | Scope | Writes, invisibly |
| --- | --- | --- | --- |
| see what EDGAR holds, and whether anything can be parsed out of it | `find_filings` | research | nothing — it fetches no document |
| take some of it | `ingest_filings` | full | an `ingestion_job` with no schedule, and one `job_execution` |
| keep taking it | `subscribe_filings` | full | an `ingestion_job_definition` and a `job_schedule` over it |
| stop | `unsubscribe_filings` | full | removes that schedule, or every schedule over one definition |
| know whether any of it is actually running | `list_subscriptions` | research | reads schedules, joined to definitions and their last execution |
| know how much of a window is covered, and by how many filers | `check_coverage` | research | nothing |

## Reading what is there

| The caller wants to | Tool | Scope |
| --- | --- | --- |
| know which companies this corpus holds | `list_companies` | data |
| open one filer: what it filed, and what is held for it | `read_company` | data |
| see one document's shape — which sections it has | `list_sections` | data |
| read one section | `read_section` | data |
| find where a phrase appears | `search_documents` | data |
| read the tagged numbers in one filing | `read_facts` | data |
| check a number they read in prose against the tagged one | `check_number` | data |

## Asking a question of it

| The caller wants to | Tool | Scope | Touches |
| --- | --- | --- | --- |
| define an indicator | `define_metric` | authoring | a `transform_job_definition` filling `metrics.<id>`, run straight away so it answers |
| see which indicators exist | `list_metrics` | data | **reads the metrics stage** -- the stage is the registry, so this cannot disagree with the pipeline |
| drop one | `remove_metric` | authoring | removes the definition and the transform behind it |
| ask who passed, on a date or over a window | `screen_companies` | research | takes your clauses inline, or the id of a saved filter -- never both; reads stored metric values and **only** stored ones -- a value computed on the call leaves no row, so nothing could trace it |
| see the filters this Qanat's operator saved | `list_filters` | research | reads `filters.yaml` |
| keep a filter so it can be run again by name | `save_filter` | authoring | one entry in `filters.yaml`. **No table and no clock**: a filter is saved clauses, run on demand |
| drop one | `remove_filter` | authoring | removes the entry, and nothing else, because nothing held its results |

## Trusting the answer

| The caller wants to | Tool | Scope | Touches |
| --- | --- | --- | --- |
| know why a number is what it is | `trace_table` | data | reads the execution on the row, up to its job, its definition and that definition's version |
| know whether what they are reading is current | `stale_tables` | data | reads the applied definition versions |
| see what tables exist and what writes each | `list_tables` | data | reads the definitions |
| see one table's columns and clock | `describe_table` | data | — |
| look at some rows | `sample_table` | data | — |
| judge whether a column is usable at all — fill, spread, range | `profile_table` | data | — |
| see what has been happening in this project | `list_executions` | full | reads `job_execution`, each row naming its job and attempt |

**`list_executions` is the general case and `list_subscriptions` is the common
one.** The question a caller actually asks is *"did my subscription run?"*, and
that is answered where the subscription is. `list_executions` answers the other
one: an agent arriving at a project it did not start, needing to know what has
been going on.

## Changing the pipeline

Everything here is at `authoring` or `full`, which is a rung above the default:
the pipeline from a filing to a metric is the same in every project, and nobody
screening filings needs to change it.

| The caller wants to | Tool | Scope | Writes |
| --- | --- | --- | --- |
| see what will run, in what order, reading and writing what, on what clock | `list_definitions` | research | reads both kinds of definition, with their cadences joined on |
| read the code one piece of it runs | `read_script` | research | — |
| fetch something from outside into a table | `define_ingestion` | authoring | an `ingestion_job_definition`, and a `job_schedule` if a cron was given |
| compute a table from other tables | `define_transform` | authoring | a `transform_job_definition`, and a `job_schedule` if a cron was given |
| drop one | `remove_transform` | authoring | the definition, and the cadences over it |
| name a basket of symbols once | `define_universe` | full | a csv under `./universes/`, which a connector's `from_universe` reads |
| hold the project against the contract | `check_project` | full | — |
| see what would change if the files were applied, and act on it | `preview_changes` | full | drops orphan tables and applies retention, behind a flag, after listing both |
| run the graph, or one definition | `run_project` | full | opens a job and an execution per definition |

**`define` rather than `save`.** *Save* says keep this for me, and there is no
*me* here: a scope is a capability boundary and not an identity one, so this
server cannot tell two callers apart. What it writes is a **definition**, which
is what the field is called.

## Choosing the project

`qanat serve` holds a **home folder** of projects, and an MCP session works in
**one** of them at a time. Until it selects one, every other tool refuses and says
to ask the user which project, or whether to create one. After that no tool takes a
project name, so a slip cannot write into the wrong project, and every answer
starts with the project it came from. Over stdio the folder is the project and
none of these are offered.

| The caller wants to | Tool | Scope | Writes |
| --- | --- | --- | --- |
| see which projects there are, which is running a job, and which changed last | `list_projects` | data | — |
| work in one, or switch to another mid-session | `select_project` | data | — opening a project is not a change, so the order holds |
| start a new, empty one | `create_project` | full | a folder in the home folder, scaffolded as `qanat init` does |
| delete one the user no longer wants | `delete_project` | authoring | without `confirm`, nothing: it says what would go. With it, the folder |

**Delete is refused** for the project this session has selected, one another
session or program is working in, and one with a job running.

---

## The candidates that did not ship

The model has eight objects. These are the tools a mirror of it would have, and
the flow each one is missing:

| Candidate | Why not |
| --- | --- |
| `save_ingestion_job_schedule`, `remove_ingestion_job_schedule` | `subscribe_filings` and `unsubscribe_filings` are the flows, and `define_ingestion`/`define_transform` take a cron for the general case. A cadence is never the thing a caller sets on its own. |
| `list_job_definitions` | `list_definitions` is that question asked the way a caller asks it. |
| `read_job`, `read_job_execution` | `trace_table` resolves the whole chain from a row, which is what somebody holding a number wants. Nobody arrives holding an execution id. |
| `list_jobs` | A job is one definition for one instant. A caller asks about the definition (`list_definitions`) or about what happened (`list_executions`), never about the layer between. |
| `enable_schedule`, `disable_schedule` | `unsubscribe_filings` is the flow for the one cadence a caller sets. Nothing else is paused rather than removed. |
| `save_job_definition_version` | Nothing writes a version. It is recorded by a successful execution, which is the only honest moment to record it. |
| a `universes:` registry, and `universe:` on a transform | **Removed in 0.11.0.** Its only reader was `ctx.universe()` inside a transform script — a basket held by one stage of the pipeline, which is the shape `weights` and `pnl` had, and they left in 0.5.0. No shipped project declared one, and `screen_companies` refuses a named universe in as many words. The csv stayed, because a connector reads it. |

## The rule a new tool has to pass

1. **Does it need Qanat's data or Qanat's engine at all?** A tool that could be
   a function in the caller's own client does not belong here. Every tool
   definition is sent on every request.
2. **Which flow is it for?** Written down, in this file, as something a caller
   does. **An entity is not a reason for a tool.**
3. **Which scope?** No default, because leaving it to one would quietly put a new
   tool in front of an institution connected at `data`.
4. **Is it the same flow at both doors?** A tool that only one door needs is a
   tool the flow was not written for.

`data` and `research` are published contracts — an institution connects at
`data`, hosted Margiana at `research` — so a rename there is announced and an
addition there is a promise.
