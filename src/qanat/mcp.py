"""The same engine, addressed by an agent instead of by a person.

`qanat mcp` serves the service layer over MCP on stdio, and `mcp_http` serves the
same tools over HTTP. Neither one holds logic: both call `store`, `editor`,
`runner`, `plan` and `screen`, which is the whole point -- if the CLI can do
it, an agent can do it, and there is no third place where the behaviour could
quietly differ.

Six things this tries to be, because an agent needs them and a person does not:

  * **discoverable** -- `list_tables` and `describe_table` mean no documentation
    is required to find out what exists
  * **addressable** -- every table, job and run has an id
  * **structured** -- results are JSON with numbers in them, never a screenshot
  * **repeatable** -- a screen reads one as-of date, and the same date is the same answer
  * **honest in failure** -- an error says what to do next, not only what broke
  * **scoped to the caller** -- `--scope` decides which tools are offered at all

Four scopes, nested: `data` reads tables, `research` adds screens and the
coverage behind them, `full` adds ingest, subscriptions and metric definitions,
`authoring` adds the pipeline itself. One server and one service layer; the scope
only picks which tools are listed. Two reasons, and the second is the larger one.
A server that reads and a server that runs jobs on request are different things to
defend, which starts to matter the day this is hosted. And every tool definition
is sent on every request, so an agent that wants filing rows should not carry
thirty-odd definitions to use four.

**`authoring` is the top rung and the default stops below it.** The authoring
tools keep working, and the caller they serve is somebody changing the shape of
the pipeline rather than running it -- adding a stage, pointing a transform at a
different table. **It used to be the professional bringing a feed in through the
HTTP, SQL or CSV connector, and that caller is gone with those connectors.** The
pipeline from a filing to a metric is the same in every project, so somebody
screening filings has no use for the tools that rewire it -- and carrying them
means an agent can rewire it by accident. The rung is the whole of the fix: a
caller who asks for the authoring scope gets all three.

Each scope is a **published contract**. Adding a tool to `data` later changes what
somebody has already built against, so the line moves deliberately or not at all.

Transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, which is what an MCP
stdio client speaks. Nothing is written to stdout except protocol messages.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Callable
from typing import Any

from qanat import __version__

PROTOCOL = "2024-11-05"


class ToolError(Exception):
    """An error the agent can act on. The message is instruction, not decoration."""


# --------------------------------------------------------------------- session
class Session:
    """One open project. Reloaded from disk whenever a tool changes the file."""

    def __init__(self, project_path: str | None):
        from qanat.project import load

        self.path = project_path
        self.project, self.root = load(project_path)
        self._store = None
        #: The running process's state, when there is one. A served process hands it
        #: over; on stdio there is none, and that is the honest answer to "is
        #: anything running the schedules" rather than something to paper over.
        self.state = None

    @property
    def sched(self):
        """The scheduler reading this project's cron lines, or None.

        None is a fact about process lifetime. Over stdio this server is the
        client's child, so a thread it started would die with the conversation.
        """
        return getattr(self.state, "sched", None)

    @property
    def store(self):
        from qanat.store import Store

        if self._store is None:
            self._store = Store(self.project.store_url(self.root))
        return self._store

    def reload(self) -> None:
        from qanat.project import load

        self.project, self.root = load(self.path)

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None


# ----------------------------------------------------------------------- tools
TOOLS: list[dict[str, Any]] = []

#: What each renamed tool is called now, and nothing answers to both.
#:
#: **Named rather than aliased.** An alias leaves both vocabularies on the
#: surface, which is the state the rename exists to end -- and a caller holding
#: the old name would keep working while every document, every message and every
#: other caller said something else. One refusal, naming the new spelling, costs
#: one call.
#:
#: Each new name came from a flow rather than from the object it writes:
#:
#:   list_steps    -> list_definitions   "what is set up to build my tables"
#:   read_step     -> read_script         "show me the code one piece runs"
#:   list_runs     -> list_executions     "every time one of them ran"
#:   save_step     -> define_transform    "compute this table from those"
#:   save_source   -> define_ingestion    "fetch this from outside"
#:   save_universe -> define_universe     "here is the basket"
#:
#: A second pass renamed the eight tools whose names were a bare noun or verb.
#: `lineage`, `screen`, `coverage`, `run`, `check` and `plan` named a subject or
#: an act without saying what it acts on, and `pipeline` and `history` were too
#: abstract to guess from:
#:
#:   lineage       -> trace_table         joins describe_/profile_/sample_table
#:   screen        -> screen_companies    the verb was right, it had no object
#:   coverage      -> check_coverage      "call check_coverage first"
#:   check         -> check_project       not to be read as check_number
#:   run           -> run_project         the graph by default, one job by argument
#:   plan          -> preview_changes     names what you get, not what it is
#:   list_pipeline -> list_definitions    the exact word, over an abstract one
#:   list_history  -> list_executions     an attempt, not the job it attempted
#:
#: `define` rather than `save` for the reason `metrics.py` already gives: *save*
#: says keep this for me, and there is no *me* here -- a scope is a capability
#: boundary and not an identity one, so this server cannot tell two callers
#: apart. What it writes is a definition, which is what the field is called.
MOVED: dict[str, str] = {
    # the vocabulary rename
    "list_steps": "list_definitions",
    "read_step": "read_script",
    "list_runs": "list_executions",
    "save_step": "define_transform",
    "remove_step": "remove_transform",
    "save_source": "define_ingestion",
    "save_universe": "define_universe",
    # the shape rename: a bare noun or verb said nothing about what it acts on
    "lineage": "trace_table",
    "list_pipeline": "list_definitions",
    "list_history": "list_executions",
    "check": "check_project",
    "plan": "preview_changes",
    "run": "run_project",
    "screen": "screen_companies",
    "coverage": "check_coverage",
}

#: Why each one moved. Two renames happened, for different reasons, and a
#: refusal that gave the wrong one sent the caller looking for the wrong thing.
_WHY_VOCAB = (
    "It was named for the object it wrote, and `source` and `step` are retired -- "
    "`source` meant four things and `step` named a position in a sequence rather "
    "than the work. The new name is what a caller does."
)
_WHY_SHAPE = (
    "It was a bare noun or verb that did not say what it acts on. Every other tool "
    "here reads {verb}_{noun}, so a name that broke the pattern could not be "
    "guessed from the others."
)
_WHY_ABSTRACT = (
    "`pipeline` and `history` were too abstract to guess from: neither says what the "
    "call returns. The exact word does, and a tool that only enumerates a set may "
    "name it -- it is one tool over all of it, not one per object."
)
_WHY_MOVED: dict[str, str] = {
    **{k: _WHY_VOCAB for k in ("list_steps", "read_step", "list_runs", "save_step",
                               "remove_step", "save_source", "save_universe")},
    **{k: _WHY_SHAPE for k in ("lineage", "check", "plan", "run", "screen", "coverage")},
    **{k: _WHY_ABSTRACT for k in ("list_pipeline", "list_history")},
}

#: The scopes, narrowest first. Each one contains the one before it, so a server
#: started at `research` offers the `data` tools too. The order in this tuple *is*
#: the nesting; nothing else encodes it.
#:
#: `authoring` sits above the default on purpose. It is reached by asking for it.
SCOPES: tuple[str, ...] = ("data", "research", "full", "authoring")

#: What `qanat mcp` offers when nothing says otherwise. Not the widest scope:
#: the tools that rewire the pipeline are a rung above, because the pipeline from
#: a filing to a metric is the same in every project and nobody screening filings
#: needs to change it.
DEFAULT_SCOPE = "full"

_RANK = {name: i for i, name in enumerate(SCOPES)}


class ScopeError(ValueError):
    """A scope name that is not one of the three."""


def rank(scope: str) -> int:
    """The nesting position of a scope name, or a message naming the three."""
    try:
        return _RANK[scope]
    except KeyError:
        raise ScopeError(
            f"no scope called '{scope}'. They are: {', '.join(SCOPES)}") from None


def tools_for(scope: str) -> list[dict[str, Any]]:
    """Every tool a caller connecting at `scope` may see, in declaration order."""
    ceiling = rank(scope)
    return [t for t in TOOLS if _RANK[t["scope"]] <= ceiling]


def tool(name: str, description: str, schema: dict[str, Any], scope: str,
         writes: bool = False):
    """Register one tool. `scope` has no default on purpose.

    A new tool has to answer two questions before it exists: rule 3 -- does it
    need Qanat's data or Qanat's engine at all -- and this one, which caller is
    allowed to see it. Leaving the second to a default would quietly put every
    new tool in front of an institution that connected at `data`.
    """
    rank(scope)

    def wrap(fn: Callable[[Session, dict], Any]):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": schema.get("properties", {}),
                            "required": schema.get("required", [])},
            "handler": fn,
            "writes": writes,
            "scope": scope,
        })
        return fn

    return wrap


def _need(args: dict, key: str) -> Any:
    if key not in args or args[key] in (None, ""):
        raise ToolError(f"'{key}' is required. Call tools/list to see this tool's arguments.")
    return args[key]


def _known_table(s: Session, ref: str) -> str:
    if ref in s.project.tables():
        return ref
    raise ToolError(
        f"no table '{ref}' in this project. Known tables: {', '.join(s.project.tables()) or '(none)'}"
    )


def _catalog(fn, *a, **kw):
    """A catalog read, with its refusal turned into one the agent can act on."""
    from qanat.catalog import CatalogError

    try:
        return fn(*a, **kw)
    except CatalogError as exc:
        raise ToolError(str(exc)) from exc


# ---- discover ----------------------------------------------------------------
@tool("list_tables", "Every table this project declares, with its stage, producer and row count.",
      {"properties": {}},
      scope="data")
def _list_tables(s: Session, args: dict) -> Any:
    from qanat.catalog import tables

    return tables(s.store, s.project)


@tool("describe_table", "One table's columns, types, row count and time column.",
      {"properties": {"ref": {"type": "string", "description": "stage.table"}}, "required": ["ref"]},
      scope="data")
def _describe_table(s: Session, args: dict) -> Any:
    from qanat.catalog import describe

    return _catalog(describe, s.store, s.project, _need(args, "ref"))


@tool("sample_table", "Rows from a table. With as_of, only rows that existed at that timestamp.",
      {"properties": {"ref": {"type": "string"}, "limit": {"type": "integer", "default": 20},
                      "as_of": {"type": "string", "description": "ISO timestamp"}},
       "required": ["ref"]},
      scope="data")
def _sample_table(s: Session, args: dict) -> Any:
    from qanat.catalog import sample

    return _catalog(sample, s.store, s.project, _need(args, "ref"),
                    limit=int(args.get("limit") or 20), as_of=args.get("as_of") or None)


@tool("profile_table",
      "What is in a table, column by column: how much of each is filled, how many distinct "
      "values it holds, and the range it spans. Use this instead of paging rows through "
      "sample_table to answer 'how many symbols', 'what dates does this cover', 'where are the "
      "holes' -- it is computed in the database, so it costs the same on a million rows as on a "
      "thousand.",
      {"properties": {"ref": {"type": "string"}}, "required": ["ref"]},
      scope="data")
def _profile_table(s: Session, args: dict) -> Any:
    from qanat.catalog import profile

    return _catalog(profile, s.store, s.project, _need(args, "ref"))


@tool("trace_table", "What feeds a table, what it feeds, and everything downstream that "
      "breaks if it breaks.",
      {"properties": {"ref": {"type": "string"}}, "required": ["ref"]},
      scope="data")
def _lineage(s: Session, args: dict) -> Any:
    from qanat.catalog import trace

    return _catalog(trace, s.project, _need(args, "ref"))


@tool("list_definitions",
      "Every definition in the graph -- ingestion and transform -- in dependency order, "
      "each with the cadences pointing at it. `stage` keeps only the transforms writing "
      "into one stage, which is how you find what produces a given table: there is no "
      "separate name for those, the stage is what identifies them. "
      "**Each transform also reports how far its output is filled against its inputs** "
      "-- `filled_to`, `inputs_to` and `behind`. A stored table only knows the days its "
      "last run covered, and a read past that quietly returns an older value, because "
      "the newest row at or before the date asked is still the rule. So a metric a week "
      "behind answers last week's number; `behind: true` is how you find that out "
      "instead of being surprised by it.",
      {"properties": {
          "stage": {"type": "string",
                    "description": "keep only transforms writing into this stage, "
                                   "e.g. normalized"},
      }, "required": []},
      scope="research")
def _list_steps(s: Session, args: dict) -> Any:
    from qanat.catalog import definitions

    return definitions(s.store, s.project, str(args.get("stage") or ""))


@tool("read_script", "The script a transform runs.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]},
      scope="research")
def _read_step(s: Session, args: dict) -> Any:
    from qanat.catalog import script

    return _catalog(script, s.project, s.root, _need(args, "id"))


# ---- validate and plan -------------------------------------------------------
@tool("check_project", "Hold the project against the stage contract. Errors block a run.",
      {"properties": {}},
      scope="full")
def _check(s: Session, args: dict) -> Any:
    from qanat.project import validate

    s.reload()
    rep = validate(s.project, s.root)
    return {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}


@tool("preview_changes",
      "What would change if the file were applied: adds, drift, orphans, and the rows "
      "every `retention:` policy is now holding past its window. With `prune: true` it "
      "acts -- it drops the orphan tables and applies those policies. **Removing is the "
      "only irreversible thing on this surface, and nothing else here does it**: the "
      "clock used to apply retention by itself once a minute, so a row that had been "
      "served went away with nothing asking for it. It is a flag on the tool that "
      "already finds them rather than a verb of its own, so you see the list from the "
      "same call that would act on it.",
      {"properties": {
          "prune": {"type": "boolean", "default": False,
                    "description": "drop the orphan tables and apply retention. "
                                   "Cannot be undone"},
      }, "required": []},
      writes=True,
      scope="full")
def _plan(s: Session, args: dict) -> Any:
    from qanat.plan import plan as plan_project
    from qanat.retention import run_retention, would_remove

    pl = plan_project(s.project, s.root, s.store)
    out: dict[str, Any] = {
        "first_run": pl.first_run, "unchanged": pl.unchanged,
        "changes": [{"action": c.action, "target": c.target, "note": c.note,
                     "details": c.details} for c in pl.changes],
    }
    if not args.get("prune"):
        notes = []
        if pl.orphans:
            out["prune_would_drop"] = [c.target for c in pl.orphans]
            notes.append(f"{len(pl.orphans)} orphan table(s) nothing produces any more")
        ageing = [r for r in would_remove(s.store, s.project) if r["rows"]]
        if ageing:
            out["retention_would_remove"] = ageing
            notes.append(
                f"{sum(r['rows'] for r in ageing):,} row(s) past a retention window, in "
                f"{', '.join(r['table'] for r in ageing)}")
        if notes:
            out["note"] = (
                "; ".join(notes) + ". Call again with prune: true to act, "
                "which cannot be undone")
        return out

    dropped = []
    for change in pl.orphans:
        rows = s.store.drop(change.target)
        s.store.event("warn", "plan", f"dropped {change.target} ({rows:,} rows)")
        dropped.append({"table": change.target, "rows": rows})
    forgotten = [c.target for c in pl.removes]
    if forgotten:
        s.store.forget_state(forgotten)
    out["pruned"] = dropped
    out["forgotten"] = forgotten
    out["retention_removed"] = {k: v for k, v in run_retention(s.store, s.project).items() if v}
    return out


# ---- run ---------------------------------------------------------------------
@tool("run_project",
      "Run the graph once, or one definition. With as_of, screen that single pass. **What "
      "you name is a definition, not a job**: a job is one definition for one exact moment "
      "and this makes one, now. The result says which job and which execution it opened, so "
      "the rows it landed can be traced without searching a log.",
      {"properties": {
          "definition": {"type": "string",
                         "description": "one definition id; omit for the whole graph"},
          "as_of": {"type": "string"}, "seed": {"type": "integer"}}},
      writes=True,
      scope="full")
def _run(s: Session, args: dict) -> Any:
    from qanat.runner import run

    return run(s.store, s.project, s.root, definition=args.get("definition") or None,
               as_of=args.get("as_of") or None, seed=args.get("seed"))


@tool("screen_companies",
      "Which companies passed a filter, on one date or on every date in a window. "
      "Returns the list, **the metric values the verdict was reached on**, each company's "
      "own forward return, how many could be evaluated at all, and an `inputs` block "
      "naming the definition versions it actually read and the corpus range it ran over. "
      "Writes nothing: store that block beside whatever you keep, because nothing here "
      "keeps it for you. `inputs.id` is a digest of those inputs, so the same inputs "
      "give the same id and a changed id means something underneath moved. Each row "
      "carries the `ticker` and the company `name` beside the filer id, so an answer "
      "never has to be joined back onto `list_companies`. "
      "`companies` bounds who is considered: a list of tickers or CIKs you pass with the "
      "call, built from list_companies. It is never stored, because which companies you "
      "care about is yours to keep. It widens the answer as well as narrowing it: a name "
      "you asked about that has no value for one of your metrics comes back as a row that "
      "could not be evaluated, instead of being quietly absent, and a name this project "
      "does not hold at all is reported rather than dropped. **`from_universe` is the "
      "other way in**, and the distinction is who owns the list: it names a csv under "
      "./universes/ that this deployment already holds and an ingestion job already reads, "
      "so two hundred names are not pasted into every call. Pass one or the other, never "
      "both. Your own list stays inline; a list this project owns may be named. "
      "`clauses` is a flat list -- `[{metric, op, value, metric_version?}]`, and every "
      "one of them has to pass. **`filter` is the other way in**: the id of a filter the "
      "operator saved in this project (see list_filters), and the same distinction as "
      "`companies` and `from_universe` -- your own clauses stay inline; a filter this "
      "project owns may be named. Pass one or the other, never both: to change a saved "
      "filter's thresholds for one screen, send its clauses changed. The answer's "
      "`filter` says which saved filter ran, or null for inline clauses, and `applied` "
      "comes back with the definition version each clause actually read, which is the "
      "part you could not have known. A clause is never a `{filter, clauses}` wrapper; "
      "that shape is refused. A clause may pin `metric_version`, and then bumping a "
      "metric cannot change what your rule computes.",
      {"type": "object",
       "properties": {
           "clauses": {"type": "array",
                       "description": "each {metric, op, value, metric_version?}. All of "
                                      "them have to pass",
                       "items": {"type": "object"}},
           "filter": {"type": "string",
                      "description": "instead of `clauses`: the id of a filter saved in "
                                     "this project, from list_filters"},
           "as_of": {"type": "string", "description": "one date, YYYY-MM-DD"},
           "from": {"type": "string", "description": "start of a window"},
           "to": {"type": "string", "description": "end of a window"},
           "every": {"type": "string", "default": "1y",
                     "description": "gap between dates in a window"},
           "from_universe": {
               "type": "string",
               "description": "instead of `companies`: the name of a csv under "
                              "./universes/ that this project holds, written by "
                              "define_universe. The same list an ingestion job reads"},
           "companies": {"type": "array",
                        "description": "tickers or CIKs. Each entry may also be "
                                       "{symbol, from, to}, and then membership is "
                                       "point-in-time -- which is what stops a list of "
                                       "today's winners being applied to the past",
                        "items": {}},
       },
       "required": []},
      "research")
def _screen(s: Session, args: dict) -> Any:
    from qanat.screen import ScreenError, run

    # Neither `filter` nor `clauses` is refused inside `run` rather than answered:
    # an empty call used to come back `subjects: 0, passed: 0` -- which reads as
    # "nobody passed" rather than "you called it wrong".
    try:
        return run(
            s.store, s.project,
            filter=args.get("filter") or None, clauses=args.get("clauses"),
            as_of=args.get("as_of"), frm=args.get("from"), to=args.get("to"),
            every=args.get("every") or "1y",
            companies=args.get("companies"),
            from_universe=args.get("from_universe"),
            root=s.root,
        )
    except ScreenError as exc:
        raise ToolError(str(exc)) from exc


@tool("list_filters",
      "The filters the operator saved in this project, each with its clauses. Run one "
      "with screen_companies(filter=<id>). **They belong to whoever runs this Qanat**, "
      "like the metric set, and every caller reads the same ones: a scope is not an "
      "identity, so nothing here is one user's. Your own rule stays with you and crosses "
      "as `clauses`.",
      {"properties": {}, "required": []},
      "research")
def _list_filters(s: Session, args: dict) -> Any:
    from qanat.filters import index

    return index(s.project)


@tool("save_filter",
      "Save a filter in this project's filters.yaml, or replace the one with that id. "
      "Every clause is checked as a screen would check it -- the comparison, a number for "
      "the value, a digest for a pin, and a metric this project defines -- so a filter "
      "that cannot run is refused here rather than found out about on somebody's screen. "
      "**A filter is not a transform**: it writes no table and runs on no clock; "
      "screen_companies runs it on demand against the stored metric values. It carries "
      "no version number -- the screen answer reports the clauses it applied.",
      {"properties": {
          "id": {"type": "string", "description": "lower case, e.g. quality_compounders"},
          "clauses": {"type": "array",
                      "description": "each {metric, op, value, metric_version?}. All of "
                                     "them have to pass",
                      "items": {"type": "object"}},
          "description": {"type": "string",
                          "description": "what the filter is for, in a reader's words"},
      }, "required": ["id", "clauses"]},
      writes=True,
      scope="authoring")
def _save_filter(s: Session, args: dict) -> Any:
    from qanat.filters import FilterError, save

    try:
        out = save(s.project, s.root, str(_need(args, "id")), args.get("clauses"),
                   description=str(args.get("description") or ""))
    except FilterError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out


@tool("remove_filter",
      "Drop a saved filter from filters.yaml. Nothing else moves: no table held its "
      "results, so there is nothing to clean up behind it.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]},
      writes=True,
      scope="authoring")
def _remove_filter(s: Session, args: dict) -> Any:
    from qanat.filters import FilterError, remove

    try:
        out = remove(s.project, s.root, str(_need(args, "id")))
    except FilterError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out


@tool("check_coverage",
      "The dates a screen can be answered for, and what sets the bound. Call this before "
      "asking for a window: a read as of a date sees the rows published on or before it, "
      "so before the oldest row there is nothing to read and a screen there returns a list "
      "of nobody -- which is not the same answer as nobody passing. Reports the range per "
      "table as well as overall, so a metric that reads two tables shows which one is the "
      "short side. Writes nothing.",
      {"type": "object",
       "properties": {
           "metrics": {"type": "array", "items": {"type": "string"},
                       "description": "only these metrics. Omit for every one defined"},
       },
       "required": []},
      scope="research")
def _coverage(s: Session, args: dict) -> Any:
    from qanat.screen import ScreenError, coverage

    try:
        return coverage(s.store, s.project, args.get("metrics") or None)
    except ScreenError as exc:
        raise ToolError(str(exc)) from exc


@tool("list_metrics",
      "Every metric this project holds: its name, the table its values live in, the "
      "definition that fills it, what that definition reads, its unit and description, and "
      "`version` -- the digest of the definition, which is what a value\'s lineage "
      "resolves to. **One metric is one table**, and the metrics stage is the registry: "
      "this is read off what the pipeline writes, so a metric cannot be listed here and "
      "missing from the pipeline. A metric is shared arithmetic -- the same number for "
      "every caller -- and the threshold you compare it against is yours and stays with "
      "you. Pass `version` back to `screen` as a clause\'s `metric_version` to read an "
      "answer as it stood.",
      {"type": "object", "properties": {}}, "data")
def _list_metrics(s: Session, args: dict) -> Any:
    from qanat.metrics import listing

    return listing(s.project, s.store)


# ---- metrics -----------------------------------------------------------------
@tool("define_metric",
      "Define a metric, or replace the formula of one that exists. `sql` selects "
      "`subject`, `date` and `value` -- **`date` is the day each value became true**, and "
      "it is what makes a metric a series rather than an answer to one call. The formula "
      "is run before it is stored, so a syntax error or an unknown column comes back as "
      "the database's own message rather than as a definition that fails on somebody "
      "else's screen, and it is run again straight after so the metric answers "
      "immediately. **One metric is one table**: this writes a transform job filling "
      "`<metrics stage>.<id>`, which means another metric can read it -- a rank reads the "
      "metric it ranks. The table is keyed and never rewritten, so replacing a formula "
      "appends its values beside the old ones and each row still names the execution that "
      "wrote it: **there is no version to bump**, because the version is the definition's "
      "digest and it moves when the formula moves. A formula containing `:as_of` is "
      "refused -- that kind of metric stored nothing, so it had no lineage and could be "
      "read as of a date before its inputs were public. The threshold you compare a "
      "metric against is not part of a definition: it is yours, and it travels with the "
      "screen call.",
      {"type": "object",
       "properties": {
           "id": {"type": "string",
                  "description": "lower case, e.g. pe. It is also the table's name"},
           "sql": {"type": "string",
                   "description": "the formula, selecting subject, date and value. Name "
                                  "tables as SQL does -- normalized__facts, not "
                                  "normalized.facts"},
           "unit": {"type": "string", "description": "e.g. ratio, USD, percent"},
           "description": {"type": "string",
                           "description": "what the number means. This is the part a "
                                          "reader cannot recover from the SQL"},
           # Declared so that passing it gets the reason rather than silence. A
           # caller written against the old surface reaches for this first.
           "version": {"type": "integer",
                       "description": "**refused.** A metric's version is the digest "
                                      "of its definition, which moves when the formula "
                                      "moves. An integer beside it was a second version "
                                      "free to disagree, and it did"},
       },
       "required": ["id", "sql"]},
      writes=True,
      scope="authoring")
def _define_metric(s: Session, args: dict) -> Any:
    from qanat.metrics import MetricError, define

    if args.get("version") is not None:
        raise ToolError(
            "there is no `version` to pass. A metric's version is the digest of its "
            "definition, which moves when the formula moves -- an integer beside it was "
            "a second version free to disagree, and measured, it did: halving a formula "
            "appended 1,553 new values all still labelled version 1. Replacing a formula "
            "is not destructive here, because the table is keyed and never rewritten"
        )
    try:
        out = define(
            s.store, s.project, s.root,
            mid=str(_need(args, "id")), sql=str(_need(args, "sql")),
            unit=str(args.get("unit") or ""),
            description=str(args.get("description") or ""),
        )
    except MetricError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out


@tool("remove_metric",
      "Drop a metric: its definition, the script behind it, and any cadence over it. "
      "Refused unless you pass force: a filter naming this metric is one caller's own "
      "criteria and is held by that caller, not here, so this server cannot check whether "
      "anything still points at it -- and whoever holds the filter is the only one who "
      "can. **The values already written are left where they are.** They name the "
      "executions that wrote them and stay readable: removing a definition is not a "
      "reason to unsay what somebody was already shown.",
      {"type": "object",
       "properties": {
           "id": {"type": "string"},
           "force": {"type": "boolean", "default": False},
           "version": {"type": "integer",
                       "description": "**refused.** One metric is one table and its "
                                      "version is its definition's digest, so there are "
                                      "no numbered versions sitting beside each other"},
       },
       "required": ["id"]},
      writes=True,
      scope="authoring")
def _remove_metric(s: Session, args: dict) -> Any:
    from qanat.metrics import MetricError, remove

    if args.get("version") is not None:
        raise ToolError(
            "there is no `version` to remove. One metric is one table and its version is "
            "its definition's digest, so there are no numbered versions sitting beside "
            "each other to drop one of"
        )
    try:
        out = remove(
            s.project, s.root, mid=str(_need(args, "id")),
            force=bool(args.get("force")),
        )
    except MetricError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out


# ----------------------------------------------------------- the reading surface
def _reading(fn, s, args, **kw):
    from qanat.reading import ReadingError
    try:
        return fn(s.store, s.project, **kw)
    except ReadingError as exc:
        raise ToolError(str(exc)) from exc


@tool("list_companies",
      "Which companies this project holds filings for: filer, ticker, name, how many "
      "filings, which forms, and the dates they span. **This is what you build a screen's "
      "`universe` from** -- a name this project does not hold is a company quietly missing "
      "from an answer, and nothing else on this surface lists them. `query` matches a "
      "ticker or a company name.",
      {"type": "object",
       "properties": {
           "query": {"type": "string", "description": "match a ticker or a company name"},
           "limit": {"type": "integer", "default": 100,
                     "description": "most companies, the ones with most filings first"},
       },
       "required": []}, "data")
def _list_companies(s: Session, args: dict) -> Any:
    from qanat.reading import list_companies
    return _reading(list_companies, s, args, query=args.get("query"),
                    limit=int(args.get("limit") or 100))


@tool("read_company",
      "One filer's way in: which filings this project holds for it, each one's sections "
      "with their lengths and the rule that found each boundary, and which concepts are "
      "tagged in it. Takes a ticker or a CIK -- a screen returns CIKs, so its output can "
      "be handed straight back in. **Returns a map and no bodies**: read_section pages the "
      "text and read_facts returns the numbers. Start here when you have a company and a "
      "question; every other reading tool starts from a document.",
      {"type": "object",
       "properties": {
           "filer": {"type": "string", "description": "a ticker, or a CIK"},
           "form": {"type": "string", "description": "only this form, e.g. 10-K"},
           "from": {"type": "string", "description": "earliest period, YYYY-MM-DD"},
           "to": {"type": "string", "description": "latest period, YYYY-MM-DD"},
           "limit": {"type": "integer", "default": 20,
                     "description": "most filings, newest filed first"},
           "concept": {"type": "string",
                       "description": "keep only concepts whose name contains this"},
           "concepts": {"type": "integer", "default": 50,
                        "description": "most concept names per filing, most-tagged first"},
       },
       "required": ["filer"]}, "data")
def _read_company(s: Session, args: dict) -> Any:
    from qanat.reading import read_company
    return _reading(read_company, s, args, filer=_need(args, "filer"),
                    frm=args.get("from"), to=args.get("to"), form=args.get("form"),
                    limit=int(args.get("limit") or 20),
                    concept=args.get("concept"),
                    concepts=int(args.get("concepts") or 50))


@tool("list_sections",
      "The map of one document: which sections were found, how long each is, and "
      "which rule decided the boundary. Read this before read_section, so you pick "
      "a section knowing what it will cost.",
      {"type": "object", "properties": {"document": {"type": "string"}},
       "required": ["document"]}, "data")
def _list_sections(s: Session, args: dict) -> Any:
    from qanat.reading import list_sections
    return _reading(list_sections, s, args, document=_need(args, "document"))


@tool("read_section",
      "One section of one document, a page at a time, with the character range the "
      "page came from. Never returns a whole section at once.",
      {"type": "object",
       "properties": {"document": {"type": "string"}, "section": {"type": "string"},
                      "page": {"type": "integer", "default": 1},
                      "page_size": {"type": "integer", "default": 4000}},
       "required": ["document", "section"]}, "data")
def _read_section(s: Session, args: dict) -> Any:
    from qanat.reading import read_section
    return _reading(read_section, s, args,
                    document=_need(args, "document"), section=_need(args, "section"),
                    page=int(args.get("page") or 1),
                    page_size=int(args.get("page_size") or 4000))


@tool("search_documents",
      "Where a phrase appears, scoped by section, entity or year. Returns locations "
      "and never bodies: find here, then read with read_section.",
      {"type": "object",
       "properties": {"query": {"type": "string"}, "section": {"type": "string"},
                      "entity": {"type": "string"}, "year": {"type": "integer"},
                      "limit": {"type": "integer", "default": 20}},
       "required": ["query"]}, "data")
def _search_documents(s: Session, args: dict) -> Any:
    from qanat.reading import search_documents
    return _reading(search_documents, s, args, query=_need(args, "query"),
                    section=args.get("section"), entity=args.get("entity"),
                    year=args.get("year"), limit=int(args.get("limit") or 20))


@tool("read_facts",
      "The tagged numbers in one document: concept, dims, both period dates, value, "
      "unit and when it became public.",
      {"type": "object",
       "properties": {"document": {"type": "string"}, "concept": {"type": "string"},
                      "limit": {"type": "integer", "default": 200}},
       "required": ["document"]}, "data")
def _read_facts(s: Session, args: dict) -> Any:
    from qanat.reading import read_facts
    return _reading(read_facts, s, args, document=_need(args, "document"),
                    concept=args.get("concept"), limit=int(args.get("limit") or 200))


@tool("check_number",
      "Given a number you read in prose, the tagged numbers nearest it and the "
      "difference. It does not say which is right: a gap means either the prose was "
      "read wrong, or the two are different measures of the same subject.",
      {"type": "object",
       "properties": {"document": {"type": "string"}, "value": {"type": "number"},
                      "within": {"type": "number", "default": 0.25}},
       "required": ["document", "value"]}, "data")
def _check_number(s: Session, args: dict) -> Any:
    from qanat.reading import check_number
    return _reading(check_number, s, args, document=_need(args, "document"),
                    value=float(_need(args, "value")),
                    within=float(args.get("within") or 0.25))


@tool("stale_tables",
      "Tables whose rows were computed by a job that has changed since. Nothing is deleted when "
      "a transform is edited -- the rows simply stop being current. Anything reading them is "
      "reading "
      "yesterday's answer until the pipeline is run again.",
      {"properties": {}, "required": []},
      scope="data")
def _stale_tables(s: Session, args: dict) -> Any:
    from qanat.plan import plan as plan_project

    pl = plan_project(s.project, s.root, s.store)
    stale = sorted(pl.stale(s.project, s.store))
    return {
        "stale": stale,
        "why": [{"job": c.target, "what_moved": c.details or [c.note]} for c in pl.updates],
        "fix": "run. The tables are rebuilt from the raw rows, which a screen never touches",
    } if stale else {"stale": [], "fix": "nothing to do: every table matches the file"}


# ---- filings -----------------------------------------------------------------
@tool("find_filings",
      "What EDGAR holds for these companies, and whether facts can be derived from each "
      "one. Company, form and period in; accession, form, both dates, whether this project "
      "already holds it and an `ingestable` label out. Writes nothing and fetches no "
      "document, so this is the cheap call to open with. `form` is optional and "
      "unrestricted -- the submissions index is uniform across every form a filer uses. "
      "An empty list means that company filed none of those in that period, which is an "
      "answer and not an error.",
      {"type": "object",
       "properties": {
           "companies": {"type": "array", "items": {"type": "string"},
                         "description": "tickers or CIKs. A CIK is the stable one"},
           "form": {"type": "string",
                    "description": "one form, e.g. 10-K, 10-Q, 8-K, 20-F. Omit for every form"},
           "from": {"type": "string", "description": "earliest period, YYYY-MM-DD"},
           "to": {"type": "string", "description": "latest period, YYYY-MM-DD"},
           "limit": {"type": "integer", "default": 50,
                     "description": "most rows per company, newest filed first"},
       },
       "required": ["companies"]},
      scope="research")
def _find_filings(s: Session, args: dict) -> Any:
    from qanat.filings import FilingsError, find

    companies = args.get("companies")
    if not companies:
        raise ToolError(
            "'companies' is required: a list of tickers or CIKs. "
            "Call tools/list to see this tool's arguments.")
    try:
        return find(
            s.store, s.project, [str(c) for c in companies],
            form=args.get("form"), frm=args.get("from"), to=args.get("to"),
            limit=int(args.get("limit") or 50),
        )
    except (FilingsError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@tool("ingest_filings",
      "Fetch the chosen filings and land the ones in scope. Each entry is {company, "
      "accession}, straight from find_filings. Only the 10-K and the 10-Q are parsed into "
      "the corpus today; every other form is fetched, reported with the taxonomy it "
      "actually holds, and refused with its pile named. A form with nothing to parse lands "
      "zero rows and says why rather than raising.",
      {"type": "object",
       "properties": {
           "filings": {"type": "array", "items": {"type": "object"},
                       "description": "each {company, accession}, as find_filings returns"},
           "cache": {"type": "string",
                     "description": "where the gzipped bodies land, relative to the project"},
       },
       "required": ["filings"]},
      writes=True,
      scope="full")
def _ingest_filings(s: Session, args: dict) -> Any:
    from qanat.filings import FilingsError, ingest

    chosen = args.get("filings")
    if not chosen:
        raise ToolError(
            "'filings' is required: a list of {company, accession}. Call find_filings "
            "first and pass the rows it returns.")
    try:
        return ingest(s.store, s.project, s.root,
                      [dict(c) for c in chosen], cache=args.get("cache"))
    except (FilingsError, ValueError) as exc:
        raise ToolError(str(exc)) from exc


@tool("subscribe_filings",
      "The same selection as find_filings, pointed forwards: when a matching filing "
      "appears, it is ingested. The interval and the hour are yours. A new selection "
      "writes its own subscription beside whatever is already polling -- several may "
      "feed one documents table, and every row names the execution that landed it -- "
      "but not one that polls a company and form something else already polls on a "
      "clock, because that filing would be fetched twice and landed once. "
      "Calling this twice with the same selection changes that subscription's cadence "
      "rather than adding a second one. `forms` is a list so one "
      "subscription covers both of the forms that can be ingested. Call "
      "list_subscriptions afterwards: a schedule nothing reads is worse than no "
      "schedule, and that tool is the only thing that can tell you which you have.",
      {"type": "object",
       "properties": {
           "companies": {"type": "array", "items": {"type": "string"},
                         "description": "tickers or CIKs"},
           "forms": {"type": "array", "items": {"type": "string"}, "default": ["10-K"],
                     "description": "10-K and 10-Q. A subscription lands rows, so it is "
                                    "refused for a form nothing can parse"},
           "from": {"type": "string", "description": "earliest period to accept"},
           "to": {"type": "string", "description": "latest period to accept"},
           "every": {"type": "string", "enum": ["daily", "monthly"], "default": "daily"},
           "hour": {"type": "integer", "default": 6,
                    "description": "hour of the day, 0-23, UTC"},
           "years": {"type": "integer", "default": 1,
                     "description": "how far back each pass looks, per form. A forward "
                                    "subscription wants a small number"},
           "from_universe": {
               "type": "string",
               "description": "instead of `companies`: the name of a csv under "
                              "./universes/ this project holds. The definition stores "
                              "the name, so adding a symbol to that file changes what "
                              "is fetched without editing the subscription"},
       },
       "required": []},
      writes=True,
      scope="full")
def _subscribe_filings(s: Session, args: dict) -> Any:
    from qanat.filings import FilingsError, subscribe

    companies = args.get("companies")
    named = args.get("from_universe")
    if not companies and not named:
        raise ToolError("'companies' is required: a list of tickers or CIKs -- or "
                        "'from_universe' naming a list this project holds. "
                        "Call tools/list to see this tool's arguments.")
    try:
        out = subscribe(
            s.project, s.root,
            [str(c) for c in companies] if companies else None,
            from_universe=str(named) if named else None,
            # `forms`, and only `forms`. It also read `form` -- a word the
            # schema does not advertise -- so a caller using the singular worked
            # while every document said otherwise. A quiet alias is the thing
            # `source` and `step` were retired for.
            forms=args.get("forms") or "10-K",
            frm=args.get("from"), to=args.get("to"),
            every=str(args.get("every") or "daily"),
            hour=int(args.get("hour") if args.get("hour") is not None else 6),
            years=int(args.get("years") or 1),
        )
    except (FilingsError, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out


@tool("list_subscriptions",
      "What is subscribed, each one's last run and next due time, and whether anything is "
      "running them at all. The last part is the point: over stdio nothing is, because this "
      "server is your client's child and a clock started here would die with the "
      "conversation. Say so rather than saying 'updating daily'.",
      {"type": "object", "properties": {}, "required": []},
      scope="research")
def _list_subscriptions(s: Session, args: dict) -> Any:
    from qanat.filings import subscriptions

    return subscriptions(s.store, s.project, s.sched)


@tool("unsubscribe_filings",
      "Stop a subscription. The documents it already landed stay in the corpus -- those are "
      "data, not wiring.",
      {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
      writes=True,
      scope="full")
def _unsubscribe_filings(s: Session, args: dict) -> Any:
    from qanat.filings import FilingsError, unsubscribe

    try:
        out = unsubscribe(s.project, s.root, _need(args, "id"))
    except FilingsError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return out



@tool("list_executions",
      "Recent job executions, with status and row counts. Each one names the job it was "
      "an attempt at, and the attempt number -- so three rows under one job id are three "
      "tries at the same scheduled moment rather than three unrelated passes.",
      {"properties": {"limit": {"type": "integer", "default": 30}}},
      scope="full")
def _list_runs(s: Session, args: dict) -> Any:
    return {"executions": s.store.recent_executions(int(args.get("limit") or 30))}


# ---- author ------------------------------------------------------------------
#: Behind an authoring tool, `schedule` is a cadence the caller asked for and
#: the tool writes a `job_schedule` row for it. The argument stays because
#: "make this run daily" is a thing a caller does; what changed is that it is
#: no longer a field welded to the selection, so a second cadence is a second
#: row and does not overwrite the first.
def _cadence(s: Session, definition_id: str, cron: str) -> dict[str, Any]:
    from qanat.editor import EditorError, save_schedule

    sid = f"{definition_id}_cron"
    try:
        save_schedule(s.project, s.root, {"id": sid, "definition": definition_id,
                                          "cron": cron, "enabled": True})
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    return {"id": sid, "cron": cron}


@tool("define_transform",
      "Add a transform, or replace one that exists. `script` is written to disk when "
      "`source` is given. `from` is a list and means it: a transform may read several "
      "tables, across any stage earlier than the one it writes. `schedule` writes a "
      "cadence row pointing at it -- several may point at one transform, and saving "
      "one does not disturb the others.",
      {"properties": {
          "id": {"type": "string"},
          "from": {"type": "array", "items": {"type": "string"},
                   "description": "every table this transform may read. `ctx.read()` "
                                  "refuses anything not named here, so this is the "
                                  "permission set as well as the dependency list"},
          "to": {"type": "array", "items": {"type": "string"}},
          "script": {"type": "string", "description": "path relative to the project root"},
          "source": {"type": "string", "description": "the script body to write"},
          "schedule": {"type": "string",
                       "description": "a cron line. Stored as its own cadence row, so "
                                      "this transform may carry more than one"},
          "options": {"type": "object"},
      }, "required": ["id", "to", "script"]},
      writes=True,
      scope="authoring")
def _save_step(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_transform

    body = args.pop("source", None)
    cron = args.pop("schedule", None)
    raw = {k: v for k, v in args.items() if v is not None}
    if body is not None:
        path = s.root / raw["script"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    try:
        written = save_transform(s.project, s.root, raw, create_script=body is None)
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    out: dict[str, Any] = {"saved": raw["id"], "warnings": written}
    if cron:
        out["schedule"] = _cadence(s, raw["id"], str(cron))
        s.reload()
    return out


@tool("remove_transform",
      "Delete a transform from the project file, and the cadences pointing at it. The "
      "script on disk is left alone.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]}, writes=True,
      scope="authoring")
def _remove_step(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, delete_transform

    try:
        written = delete_transform(s.project, s.root, _need(args, "id"))
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"removed": args["id"], "warnings": written}


@tool("define_universe",
      "Name a basket of symbols once, as a csv a connector can point at. "
      "`from_universe` on an ingestion job resolves `./universes/<id>.csv` and reads the "
      "symbols out of it, so a selection of two hundred companies is named here rather "
      "than pasted into every definition that polls them. **The file is the whole of it** "
      "-- nothing in the project refers to it by name, and a screen takes its list with "
      "the call.",
      {"properties": {
          "id": {"type": "string", "description": "lower case, e.g. tech8"},
          "symbols": {"type": "array", "items": {"type": "string"},
                      "description": "the symbols in it, written to the csv"},
          "file": {"type": "string",
                   "description": "where the csv lives, relative to the project. Defaults to "
                                  "./universes/<id>.csv"},
      }, "required": ["id", "symbols"]},
      writes=True,
      scope="full")
def _save_universe(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_universe

    uid = _need(args, "id")
    syms = [str(x).strip() for x in (_need(args, "symbols") or []) if str(x).strip()]
    try:
        path = save_universe(s.root, uid, syms, file=args.get("file"))
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    return {"saved": uid, "symbols_file": str(path.relative_to(s.root)),
            "symbols": len(syms),
            "note": "point an ingestion job at it with `options.from_universe: " + uid +
                    "`. A list with no join and leave dates is today's membership applied "
                    "to the past, which flatters every number built on it -- add `from` and "
                    "`to` columns to the csv when you know them"}


@tool("define_ingestion",
      "Add or replace an ingestion job: what to fetch, and the one table it lands in. "
      "`key` names the columns that identify one row -- a row that arrives again "
      "unchanged is not stored twice, and one that arrives changed is written beside "
      "the old one. `schedule` writes a cadence row pointing at this job.",
      {"properties": {"id": {"type": "string"},
                      "to": {"type": "array", "items": {"type": "string"}},
                      "connector": {"type": "string",
                                    "enum": ["sec", "yfinance"]},
                      "key": {"type": "array", "items": {"type": "string"}},
                      "schedule": {"type": "string",
                                   "description": "a cron line. Stored as its own cadence "
                                                  "row, and a job carries at most one -- a "
                                                  "second cadence over one selection is the "
                                                  "finer of the two doing the coarser one's "
                                                  "work twice"},
                      "options": {"type": "object"}},
       "required": ["id", "to", "connector"]}, writes=True,
      scope="authoring")
def _save_source(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_ingestion

    cron = args.pop("schedule", None)
    try:
        written = save_ingestion(s.project, s.root,
                                 {k: v for k, v in args.items() if v is not None})
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    out: dict[str, Any] = {"saved": args["id"], "warnings": written}
    if cron:
        out["schedule"] = _cadence(s, str(args["id"]), str(cron))
        s.reload()
    return out


# ------------------------------------------------------------------- transport
def _dispatch(session: Session, msg: dict, tools: list[dict]) -> dict | None:
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return _ok(mid, {
            "protocolVersion": asked if isinstance(asked, str) else PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "qanat", "version": __version__},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")}
                                   for t in tools]})
    if method == "tools/call":
        name = params.get("name")
        entry = next((t for t in tools if t["name"] == name), None)
        if entry is None:
            # A tool that exists but sits above this scope gets its own answer.
            # "no such tool" would be a lie, and the person operating the client
            # needs to know it is the connection that is narrow, not the server.
            above = next((t for t in TOOLS if t["name"] == name), None)
            if above is not None:
                return _ok(mid, _text(
                    f"'{name}' is a tool in the '{above['scope']}' scope, and this server was "
                    f"started below it. Reconnect at --scope {above['scope']}.", error=True))
            if name in MOVED:
                return _ok(mid, _text(
                    f"'{name}' is now '{MOVED[name]}'. {_WHY_MOVED[name]} Nothing answers to "
                    f"both: an alias would leave both vocabularies on the surface, which is "
                    f"what the rename ends.",
                    error=True))
            return _ok(mid, _text(
                f"no tool called '{name}'. Available: {', '.join(t['name'] for t in tools)}",
                error=True))
        try:
            out = entry["handler"](session, dict(params.get("arguments") or {}))
            return _ok(mid, _text(json.dumps(out, default=str, indent=2)))
        except ToolError as exc:
            return _ok(mid, _text(str(exc), error=True))
        except Exception as exc:  # noqa: BLE001 -- an agent gets the reason, never a dead channel
            print(traceback.format_exc(), file=sys.stderr)
            return _ok(mid, _text(f"{type(exc).__name__}: {exc}", error=True))
    if mid is None:
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def _ok(mid: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _text(body: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": body}], "isError": error}


def serve_stdio(project_path: str | None = None, scope: str = DEFAULT_SCOPE) -> int:
    """Speak MCP on stdin/stdout until the client goes away."""
    from qanat.store import set_actor

    try:
        offered = tools_for(scope)
    except ScopeError as exc:
        print(f"qanat mcp: {exc}", file=sys.stderr)
        return 2

    # Everything this process does is the agent doing it. Set once, for the whole
    # process: the scheduler overrides it per job, and the CLI never gets here.
    set_actor("agent")
    try:
        session = Session(project_path)
        _ = session.store  # open now, so a busy database is reported before a client connects
    except Exception as exc:  # noqa: BLE001
        print(f"qanat mcp: {exc}", file=sys.stderr)
        return 1

    tools = offered
    print(f"qanat mcp: {session.project.name} · scope {scope} · {len(tools)} of "
          f"{len(TOOLS)} tools", file=sys.stderr)

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                sys.stdout.write(json.dumps({
                    "jsonrpc": "2.0", "id": None,
                    "error": {"code": -32700, "message": "parse error"}}) + "\n")
                sys.stdout.flush()
                continue
            for one in msg if isinstance(msg, list) else [msg]:
                reply = _dispatch(session, one, tools)
                if reply is not None:
                    sys.stdout.write(json.dumps(reply, default=str) + "\n")
                    sys.stdout.flush()
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        session.close()
    return 0


__all__ = ["DEFAULT_SCOPE", "PROTOCOL", "SCOPES", "TOOLS", "ScopeError", "Session",
           "ToolError", "rank", "serve_stdio", "tools_for"]
