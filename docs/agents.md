# Notes on the agent surface

For how a filing becomes a number an agent can read, see [dataflow.md](dataflow.md) — the whole path, with the counts read out of the bundled example rather than estimated.

The README covers adding qanat to an MCP client. These are the parts that only matter once you are
using them. There are 33 tools at `authoring`, 28 at `full`, 20 at `research` and 14 at `data`.

## Why each tool is here

**[flows.md](flows.md) traces every one of them to something a caller does**, and
that is the only reason any of them is here. The data model went from two objects
to eight in 0.11.0 and this surface did not move, because **having an entity is
not a reason to have a tool over it** — a mirror of the model would be about
thirty-two tools, generated from the shape of the schema rather than from
anything a caller needs.

A new tool answers four questions before it exists: does it need Qanat's data or
engine at all, which flow is it for, which scope may see it, and is it the same
flow at both doors. A test holds the first three: no tool that *acts* on
one names `job`, `definition`, `schedule` or `execution` -- a `list_` tool may,
because enumerating a set is one tool over all of it and the exact word is the
only one a caller can guess -- and every tool appears in `flows.md`.

**There was a fourth: a fixed tool count per scope, and it is gone.** The numbers
were measured on `0.10.2` and then read as a rule. They were a proxy for the
thing worth holding -- that a tool exists for a flow rather than for an object --
and the flow test holds that directly. Once every scope sat exactly on its
number the ceiling could only block the next honest addition, which is not what
a budget is for. **The surface is still sized by what users do; it is the flow
that says so, not a number.**

## The agent's surface is wider than the CLI's

One tool does the same thing as a command without sharing its name: `check_project`
answers what `qanat check` answers. `preview_changes`, `run_project` and
`screen_companies` once shared a command name too, and those commands were removed — a second door onto one question
is a second place the behaviour can quietly differ, and the caller here is a program.

An agent navigates where a person reads. `qanat ls` printed stages, tables and jobs in one dump;
an agent gets `list_tables`, `list_definitions` and `describe_table` separately, and `describe_table`
plus `sample_table` to look inside one. **`ls` is gone, and so are `graph`, `preview_changes`, `prune`, `run_project`
and `screen_companies`** — each was a second door onto a question a tool already answers, and a second door
is a second place the behaviour can quietly differ. Running one prints where it went.

Going the other way, `define_transform`, `define_ingestion` and `define_universe` have no CLI at
all. You author a transform in your editor; an agent authors one through the API.

Four of the five commands have no tool. `init`, because an agent works inside a project that
already exists. `mcp` and `serve`, because they are the transport. And `schedule`, because of
process lifetime, not authority -- there is nothing for a `start_scheduler` tool to start that
would still be there after the call. `check` is the one with a tool, and `check_project` is it.
(`prune` and `ls` are gone rather than toolless: an agent does not drop tables, and `ls` was
three tools in one dump.)

**A clock cannot live in a stdio server, and that is a fact rather than a rule.** Over stdio
this server is your client's child, so a thread it started would die with the conversation —
there is nothing for a `start_scheduler` tool to start that would still be there. Under
`qanat serve` the process already outlives the request and already holds a scheduler. So
instead of a tool that starts one, `list_subscriptions` reports whether one is attached: over
stdio the honest sentence is *"this will run when the recorder runs, and nothing is running
here"*, not *"updating daily"*.

**And lifetime is only half the reason, because it stops applying one line above.** Under
`qanat serve` the process does outlive the request -- so lifetime alone never ruled out a
starter on the HTTP door. **The reason that covers both transports is that a clock is a
deployment, and no orchestrator's API manages its own deployment.** Airflow's scheduler and
Dagster's daemon are separate always-on processes; both products expose plenty of writes to an
agent, and neither offers a tool that starts its own clock. `unpause_dag` and `start_schedule`
write state a running clock reads -- Dagster's own documentation says the daemon does not spawn
processes on demand.

That is the same shape as `subscribe_filings` here: it writes the cadence into `jobs.yaml`, and
a clock reads it when there is one. **Starting that clock is part of deploying this, and
the product says so rather than pretending otherwise.**

## Nothing here judges a result

`screen_companies` returns who passed and what each name did next. It does not say whether that was good,
and no tool on this surface does.

The reading tools hold the same line in the other direction. `check_number` puts a number you
read in prose next to the tagged numbers near it and reports the difference. It never says which
is right, because a gap has two explanations — the prose was read wrong, or the two are different
measures of the same subject — and the tool can tell neither from the other.

Removed in 0.4.0: `record_trial`, `list_trials`, `read_bar`, `set_bar`, `compare`, `weights`.
Removed in 0.5.0: `backtest`, `backtest_conditions`, `list_backtests`, `report`, `period` — the
package no longer prices a basket, so there is no run to report on. The
first four were a trial ledger and a significance bar, which only mean something if the product
certifies indicators. It does not. And the arithmetic says the bar could not do that job anyway:
at fourteen annual rebalances it lands at an IC of 0.069, a real indicator is 0.02 to 0.06, so the
test fails on a true effect at any history length that is available.

`weights` went because nothing in the product simulates holding a position any more, and `compare`
because comparing two runs needed one summary number per run and there is no longer one.

**What replaces them is reporting per date and saying how many dates there were.** A number from
four rebalances and a number from four hundred are not the same claim, and the honest way to show
that is to show the count beside it rather than to divide a p-value by it.

## `profile_table` answers the question `sample_table` cannot

"How many symbols, over what dates, with how many holes" is the first thing anybody asks of a
dataset they have just connected, and paging rows to find out works on a demo and falls over on a
real panel -- five hundred names by ten years is a million rows moved so somebody can learn there
are five hundred names. `profile_table` is aggregates: fill rate, distinct count, and the range
each column spans. It costs a scan, and the answer is the same size whatever the table is.

## `define_universe` writes a file, because an agent cannot

A connector's `from_universe` option names a csv under `./universes/`, so a basket of two hundred
companies is named once rather than pasted into every definition that polls them. Writing that file
is the whole of what this tool does -- and it is a tool because **an agent reaching a project over
MCP cannot write a file**, which is what the error used to tell it to go and do.

**There is no `universes:` key any more.** It was read by `ctx.universe()` inside a transform
script, which is a basket held by one stage of the pipeline -- the shape `weights` and `pnl` had,
and they left the platform in 0.5.0. A screen takes its list with the call and refuses a named one,
because which companies you care about is yours to keep.

A list with no `from` and `to` dates is today's membership applied to the past, and every number
built on it is flattered by the names that survived. `qanat check` says so out loud.

## `sample_table` honours `as_of`

Passing an as-of date reads the table through the same point-in-time view a screen uses, so an
agent inspecting history sees what a transform would have seen on that date. Without it, an agent
reasoning about the past reasons about rows that had not happened yet.

## The reading surface is five tools and a rule each

`list_sections` reports how long each section is **before** you open one, so an agent picks
knowing the cost. `read_section` pages and never returns a whole Item: Item 1A runs past 100 KB
in a large filer, and a tool that hands that over has spent the context the agent needed to think
with. `search_documents` returns locations and never bodies — find, then read. `read_facts` gives
the tagged numbers with both period dates on them. `check_number` is the honesty check described
above.

`list_sections` also reports which rule decided each boundary. A 10-K names every Item on its
contents page and again throughout the prose, so most matches are not the heading. A boundary
tool that returns a confident wrong offset is worse than one that admits it guessed.

### Three of them read a `spans` table, and a project has to have one

`list_sections`, `read_section` and `search_documents` all read the table `corpus.spans` names,
and `read_company` returns its sections half out of the same place. `qanat init` scaffolds
`transforms/parse_spans.py` and points `corpus:` at what it writes, so a fresh project has this
already.

**A project scaffolded before 0.11.1 does not, and nothing rewrites `qanat.yaml` for it.** Three
edits, in the project that needs them:

```bash
# 1. the producer, copied out of the scaffold
python -c "from qanat.scaffold import PARSE_SPANS_PY as s; \
           open('transforms/parse_spans.py','w').write(s)"
```

```yaml
# 2. qanat.yaml -- the clock, then the corpus entry. Both, or the table is
#    either unfilterable by date or not looked in at all.
time_columns:
  normalized.spans: as_of

corpus:
  spans: normalized.spans
```

```yaml
# 3. jobs.yaml -- the transform job, beside parse_facts
transform_job_definitions:
  - id: parse_spans
    from: [raw.documents]
    to:   [normalized.spans]
    script: transforms/parse_spans.py
    options:
      reads: raw.documents
```

Then `qanat check`, and run `parse_spans` once over the documents already landed — it reads
`raw.documents` and nothing else, so it needs no re-fetch.

**Until all three are done, `read_company` says so rather than answering `sections: []`.** An
empty list is the right answer for a filing whose form has no Item vocabulary, so it is not also
the answer to a project that was never told where its spans live; the `sections` key is absent and
`sections_unavailable` carries the reason.

## `--scope`

Four scopes, nested. Each one contains the one before it, and **`full` is the default — not the
widest one.**

```bash
qanat mcp --scope data         # 17 tools
qanat mcp --scope research     # 24 tools
qanat mcp                      # 33 tools
qanat mcp --scope authoring    # 41 tools
```

| scope | adds | tools |
| --- | --- | --- |
| `data` | read the tables, list and read companies, read the documents, choose the project | `list_tables` `describe_table` `sample_table` `profile_table` `trace_table` `stale_tables` `list_metrics` `list_companies` `read_company` `list_sections` `read_section` `search_documents` `read_facts` `check_number` `list_projects` `select_project` `close_project` |
| `research` | read the graph, find filings, screen, read the saved filters | `list_definitions` `read_script` `screen_companies` `check_coverage` `find_filings` `list_subscriptions` `list_filters` |
| `full` | run it, ingest, and make a project | `check_project` `preview_changes` `run_project` `list_executions` `ingest_filings` `subscribe_filings` `unsubscribe_filings` `define_universe` `create_project` |
| `authoring` | define metrics, save filters, and rewire the pipeline | `define_metric` `remove_metric` `define_transform` `remove_transform` `define_ingestion` `save_filter` `remove_filter` `delete_project` |

**The counts here are held by a test.** `test_the_published_scope_counts_are_the_real_ones` reads
them out of this file, `dataflow.md` and the README and compares each against `tools_for`, so a
tool added at any rung fails the suite rather than leaving three documents disagreeing.

**Why `authoring` is above the default.** The pipeline from a filing to a metric is the same in
every project, so nobody screening filings needs the tools that change it — and carrying them means
an agent can change it by accident. Defining a metric sits there too, for a different reason: a
formula is the same number for every reader of a given Qanat, so there is no private definition and
the owner of one is whoever runs the deployment.

**`trace_table` and `stale_tables` stay in `data`, and they look like they should not.** Both only
mean something to a caller who may change the pipeline. But each scope is a published contract, and
taking a tool out of `data` breaks what somebody has already built against it worse than adding one
would. Moving them is a separate decision, not a cleanup.

**It is one server and one service layer.** The scope decides which tools are listed, nothing
else. Three separate servers would drift apart, and the CLI and the MCP server answering the same
way is the thing this package refuses to break.

Two reasons for the split, and the second is the larger one.

**What has to be defended.** A server that reads and a server that runs jobs on request are
different things, and the difference starts to cost money the day the server is hosted and
answering to more than one project.

**What a tool list costs.** Every definition is sent on every request. An agent that wants rows
out of one table should not carry twenty-three descriptions to use four, and a long list makes it
worse at picking from the list.

**Nothing in `research` touches `qanat.yaml`, and nothing in it writes at all.** A screen is a
query: it reads the facts, applies your thresholds and returns a list. There is nowhere in Qanat
a per-user result could correctly live, because the store cannot tell two callers apart.

**Each scope is a contract.** Adding a tool to `data` changes what somebody has already built
against, so the line moves on purpose or not at all. A tool that exists above your scope says so
by name instead of pretending not to exist -- if that message keeps appearing for the same tool,
the line is drawn in the wrong place.

`--read-only` is gone. It split the tools 23 and 10, which was a fact about the implementation and
not a promise anybody could build on.

## `qanat serve`

`qanat mcp` speaks JSON-RPC on stdin and stdout, which is the right transport for one person on
one machine and cannot be the hosted one. A request arriving at a server has nowhere to keep a
child process, and the client is somewhere else entirely.

**So the HTTP server is its own verb.** It used to be `qanat mcp --http`, and that name stopped
describing the program: over stdio there is no port and no clock, with `--http` there was both, and
**a flag that changes what the program is belongs in the verb.** `--mcp` and `--app` then name the
doors it carries, and a person who only wants the pages no longer opens a tool endpoint on their
port.

```bash
qanat serve --mcp --port 8421 --scope research --token "$QANAT_MCP_TOKEN"
qanat serve --mcp --schedule --port 8421 --token "$QANAT_MCP_TOKEN"   # plus the clock
qanat serve --app --scope research                                    # the pages, no /mcp
qanat schedule --run-now                                              # the clock, serving nothing
```

**It starts without a project.** `qanat serve` holds a home folder of projects
(`--home`, `QANAT_HOME`, or `~/qanat` -- never the folder you happen to be in),
and each MCP session works in the one it selects with
`select_project`. Until then every other tool refuses and says to ask the user
which project. `list_projects`, `create_project` and `delete_project` are the rest
of that flow; over stdio none of them is offered, because the folder is the
project.

This is MCP's Streamable HTTP transport on `/mcp`. `POST` carries JSON-RPC and gets JSON back.
`initialize` hands out an `Mcp-Session-Id` that every later request must send. `DELETE` ends a
session, and a session idle for an hour is dropped, which answers `404` and means the client should
call `initialize` again. `GET` answers `405`, because this server never sends anything on its own
and holding a stream open that will never carry a message is worse than saying so.

Three things are fixed by the command that starts it rather than by anything in a request.

**The scope.** A caller that names its own scope has no scope at all. `--scope` decides, and
`tools/list` and `tools/call` both read the same pinned list, so a tool above the line answers with
the name of the scope it needs instead of pretending not to exist.

**One project per process.** The store takes one writer, so every session on a server shares one
store behind a lock. Two projects means two processes. Many tenants means a process and a store
each. This is the real limit on how far the HTTP transport goes today.

**`--schedule` is this server with a clock.** It opens the store, starts the scheduler, and
mounts whichever doors were asked for, so one process holds the store and everything reaches the project
through it. That includes the unattended falsification pass, which used to be handed a dozen curl
endpoints and is now given `--scope research` and the tools that go with it. The scope is what
stops the pass editing the strategy it is meant to be attacking; it used to be a sentence in a
prompt.

**The clock and the server are separable, and that is the point of the flag.** They used to be
one command with `--no-schedule` to turn the clock off, and no way at all to run the clock alone
— backwards for the two deployments that exist. A recorder wants a clock and no open port:
`qanat schedule`. A hosted reader wants a server over a store something else fills:
`qanat serve --mcp`. One machine doing both jobs asks for both.

**`list_subscriptions` reports which one you have.** Over stdio nothing in the process reads a
cron line, so it says so rather than saying "updating daily".

**Who may connect.** It binds to 127.0.0.1 and refuses any other address without `--token` or
`QANAT_MCP_TOKEN`, because binding somewhere reachable and then answering anybody is not a default
worth having. The `Host` and `Origin` checks are the same either way, and they are here
for the same reason: binding to loopback does not stop a page you are visiting from pointing its
own domain at 127.0.0.1 and calling this server as if it were same-origin.

`GET /health` reports the project, the scope, how many tools that scope offers, and how many
sessions are open.

## `from:` is a list, and the tools treat it as one

A transform may read several tables, across any stage earlier than the one it writes -- a
transform job definition is `n:m`,
and the scaffold's own `portfolio` transform reads three at once. `define_transform` takes the whole list, and
it is the permission set as much as the dependency list: `ctx.read()` refuses anything not named
there. `define_transform` takes one primary table and `also_reads` for the
rest, because the shelf scripts rank on one price table and a later edit may not.
