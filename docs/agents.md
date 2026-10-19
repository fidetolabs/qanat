# Notes on the agent surface

The README covers adding qanat to an MCP client. These are the parts that only matter once you are
using them. There are 23 tools at `full`, 15 at `research` and 12 at `data`.

## The agent's surface is wider than the CLI's

Four tools share a name with a command and do the same thing: `check`, `plan`, `run` and
`screen`. Everything else differs, deliberately.

An agent navigates where a person reads. `qanat ls` prints stages, tables and jobs in one dump;
an agent gets `list_tables`, `list_steps` and `describe_table` separately, and `describe_table`
plus `sample_table` to look inside one.

Going the other way, `save_step`, `save_source` and `save_universe` have no CLI at
all. You author a step in your editor; an agent authors one through the API.

Four commands have no tool. `init` and `prune` because an agent works inside a project that
already exists and does not drop tables; `serve` because starting a scheduler is not something an
agent should do mid-question; `mcp` because it is the transport; `ls` because it is three tools
here.

## Nothing here judges a result

`screen` returns who passed and what each name did next. It does not say whether that was good,
and no tool on this surface does.

The reading tools hold the same line in the other direction. `check_number` puts a number you
read in prose next to the tagged numbers near it and reports the difference. It never says which
is right, because a gap has two explanations — the prose was read wrong, or the two are different
measures of the same subject — and the tool can tell neither from the other.

Removed in 0.4.0: `record_trial`, `list_trials`, `read_bar`, `set_bar`, `compare`, `weights`.
Removed in 0.5.0: `backtest`, `backtest_conditions`, `list_backtests`, `report`, `period` — the
package no longer prices a basket, so there is no run to report on. The
first four were a trial ledger and a significance bar, which only mean something if the product
certifies indicators. It does not. The arithmetic is in the fdtl-kb record, `O-20261018-04`: at
fourteen annual rebalances the significance bar lands at an IC of 0.069 and a real indicator is
0.02 to 0.06, so the test fails on a true effect at any history length that is available.

`weights` went because nothing in the product simulates holding a position any more, and `compare`
because comparing two runs needed one summary number per run and there is no longer one.

**What replaces them is reporting per date and saying how many dates there were.** A number from
four rebalances and a number from four hundred are not the same claim, and the honest way to show
that is to show the count beside it rather than to divide a p-value by it.

## `profile_table` answers the question `sample_table` cannot

"How many symbols, over what dates, with how many holes" is the first thing anybody asks of a
source they have just connected, and paging rows to find out works on a demo and falls over on a
real panel -- five hundred names by ten years is a million rows moved so somebody can learn there
are five hundred names. `profile_table` is aggregates: fill rate, distinct count, and the range
each column spans. It costs a scan, and the answer is the same size whatever the table is.

## `save_universe` exists because the error used to name a fix nobody had

A step that holds a basket holds itself to a universe, so the contract refuses a project that declares
none. It used to say "add one to qanat.yaml" -- which an agent reaching this project over MCP
cannot do, because there was no universe mutation anywhere. Give this one an id and some symbols
and it writes the csv.

A list with no `from` and `to` dates is today's membership applied to the past, and every number
built on it is flattered by the names that survived. `qanat check` says so out loud.

## `sample_table` honours `as_of`

Passing an as-of date reads the table through the same point-in-time view a screen uses, so an
agent inspecting history sees what a step would have seen on that date. Without it, an agent
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

## `--scope`

Three scopes, nested. Each one contains the one before it, and `full` is the default.

```bash
qanat mcp --scope data       # 12 tools
qanat mcp --scope research   # 15 tools
qanat mcp                    # 23 tools
```

| scope | adds | tools |
| --- | --- | --- |
| `data` | read the tables, and read the documents | `list_tables` `describe_table` `sample_table` `profile_table` `lineage` `stale_tables` `list_metrics` `list_sections` `read_section` `search_documents` `read_facts` `check_number` |
| `research` | read the graph, and screen | `list_steps` `read_step` `screen` |
| `full` | author, ingest, schedule | `check` `plan` `run` `list_runs` `save_step` `remove_step` `save_source` `save_universe` |

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

## `--http`

`qanat mcp` speaks JSON-RPC on stdin and stdout, which is the right transport for one person on
one machine and cannot be the hosted one. A request arriving at a server has nowhere to keep a
child process, and the client is somewhere else entirely.

```bash
qanat mcp --http --port 8421 --scope research --token "$QANAT_MCP_TOKEN"
qanat serve    --port 8421 --scope research --token "$QANAT_MCP_TOKEN"   # plus the scheduler
```

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
store behind a lock. Two projects means two processes. Many tenants means Postgres and a store
each. This is the real limit on how far the HTTP transport goes today.

**`qanat serve` is this server with a clock.** It opens the store, starts the scheduler, and
mounts the same endpoint, so one process holds the store and everything reaches the project
through it. That includes the unattended falsification pass, which used to be handed a dozen curl
endpoints and is now given `--scope research` and the tools that go with it. The scope is what
stops the pass editing the strategy it is meant to be attacking; it used to be a sentence in a
prompt.

**Who may connect.** It binds to 127.0.0.1 and refuses any other address without `--token` or
`QANAT_MCP_TOKEN`, because binding somewhere reachable and then answering anybody is not a default
worth having. The `Host` and `Origin` checks are the ones `qanat serve` uses, and they are here
for the same reason: binding to loopback does not stop a page you are visiting from pointing its
own domain at 127.0.0.1 and calling this server as if it were same-origin.

`GET /health` reports the project, the scope, how many tools that scope offers, and how many
sessions are open.

## `from:` is a list, and the tools treat it as one

A step may read several tables, across any stage earlier than the one it writes -- `Step` is `n:m`,
and the scaffold's own `portfolio` step reads three at once. `save_step` takes the whole list, and
it is the permission set as much as the dependency list: `ctx.read()` refuses anything not named
there. `save_step` takes one primary table and `also_reads` for the
rest, because the shelf scripts rank on one price table and a later edit may not.
