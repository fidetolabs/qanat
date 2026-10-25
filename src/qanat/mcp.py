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

Three scopes, nested: `data` reads tables, `research` adds screens and their
results, `full` adds authoring, ingest and scheduling. One server and one service
layer; the scope only picks which tools are listed. Two reasons, and the second is
the larger one. A server that reads and a server that runs jobs on request are
different things to defend, which starts to matter the day this is hosted. And
every tool definition is sent on every request, so an agent that wants filing rows
should not carry thirty-three definitions to use four.

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

#: The three scopes, narrowest first. Each one contains the one before it, so a
#: server started at `research` offers the `data` tools too. The order in this
#: tuple *is* the nesting; nothing else encodes it.
SCOPES: tuple[str, ...] = ("data", "research", "full")

_RANK = {name: i for i, name in enumerate(SCOPES)}


class ScopeError(ValueError):
    """A scope name that is not one of the three."""


def rank(scope: str) -> int:
    """The nesting position of a scope name, or a message naming the three."""
    try:
        return _RANK[scope]
    except KeyError:
        raise ScopeError(
            f"no scope called '{scope}'. The three are: {', '.join(SCOPES)}") from None


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


# ---- discover ----------------------------------------------------------------
@tool("list_tables", "Every table this project declares, with its stage, producer and row count.",
      {"properties": {}},
      scope="data")
def _list_tables(s: Session, args: dict) -> Any:
    out = []
    producers = s.project.producers()
    for ref in s.project.tables():
        info = s.store.table_info(ref)
        stage = s.project.stage(ref.split(".")[0])
        out.append({
            "ref": ref,
            "stage": ref.split(".")[0],
            "stage_kind": stage.kind if stage else None,
            "producer": producers.get(ref),
            "rows": info.rows if info else 0,
            "written": info is not None,
        })
    return {"project": s.project.name, "tables": out}


@tool("describe_table", "One table's columns, types, row count and time column.",
      {"properties": {"ref": {"type": "string", "description": "stage.table"}}, "required": ["ref"]},
      scope="data")
def _describe_table(s: Session, args: dict) -> Any:
    ref = _known_table(s, _need(args, "ref"))
    info = s.store.table_info(ref)
    if info is None:
        raise ToolError(f"'{ref}' is declared but has not been written yet. Run `run` first.")
    return {
        "ref": ref,
        "rows": info.rows,
        "updated_at": info.updated_at,
        "time_column": s.store.time_column(ref, s.project.time_columns.get(ref)),
        "newest": s.store.max_time(ref, s.project.time_columns.get(ref)),
        "columns": [{"name": c, "type": t} for c, t in info.columns],
    }


@tool("sample_table", "Rows from a table. With as_of, only rows that existed at that timestamp.",
      {"properties": {"ref": {"type": "string"}, "limit": {"type": "integer", "default": 20},
                      "as_of": {"type": "string", "description": "ISO timestamp"}},
       "required": ["ref"]},
      scope="data")
def _sample_table(s: Session, args: dict) -> Any:
    ref = _known_table(s, _need(args, "ref"))
    limit = int(args.get("limit") or 20)
    df = s.store.read(ref, limit=limit, as_of=args.get("as_of") or None)
    return {"ref": ref, "as_of": args.get("as_of"), "rows": len(df),
            "sample": json.loads(df.to_json(orient="records", date_format="iso"))}


@tool("profile_table",
      "What is in a table, column by column: how much of each is filled, how many distinct "
      "values it holds, and the range it spans. Use this instead of paging rows through "
      "sample_table to answer 'how many symbols', 'what dates does this cover', 'where are the "
      "holes' -- it is computed in the database, so it costs the same on a million rows as on a "
      "thousand.",
      {"properties": {"ref": {"type": "string"}}, "required": ["ref"]},
      scope="data")
def _profile_table(s: Session, args: dict) -> Any:
    ref = _known_table(s, _need(args, "ref"))
    prof = s.store.profile(ref)
    if prof is None:
        raise ToolError(f"{ref} has not been written yet, so there is nothing to profile. "
                        "Run the job that writes it first.")
    return prof


@tool("lineage", "What feeds a table, and which weights tables break if it breaks.",
      {"properties": {"ref": {"type": "string"}}, "required": ["ref"]},
      scope="data")
def _lineage(s: Session, args: dict) -> Any:
    from qanat.project import edges

    ref = _known_table(s, _need(args, "ref"))
    es = edges(s.project)
    upstream = [{"from": a, "step": st} for a, b, st in es if b == ref]
    downstream = [{"to": b, "step": st} for a, b, st in es if a == ref]

    reach, seen = [], {ref}
    frontier = [ref]
    while frontier:
        cur = frontier.pop()
        for a, b, _st in es:
            if a == cur and b not in seen:
                seen.add(b)
                reach.append(b)
                frontier.append(b)
    return {"ref": ref, "upstream": upstream, "downstream": downstream,
            "reaches": reach}


@tool("list_steps",
      "Every job in the graph -- sources and steps -- in dependency order. `stage` keeps only the "
      "steps writing into one stage, which is how you find what produces a given table: there is "
      "no separate name for those, the stage is what identifies them.",
      {"properties": {
          "stage": {"type": "string",
                    "description": "keep only steps writing into this stage, e.g. normalized"},
      }, "required": []},
      scope="research")
def _list_steps(s: Session, args: dict) -> Any:
    from qanat.runner import order

    stage = str(args.get("stage") or "").strip()
    steps = [x for x in order(s.project)
             if not stage or any(w.startswith(f"{stage}.") for w in x.writes)]
    rows = [{"id": x.id, "from": x.reads, "to": x.writes, "script": x.script,
             "universe": x.universe, "schedule": x.schedule, "options": x.options}
            for x in steps]
    return {
        "sources": [] if stage else [
            {"id": x.id, "to": x.writes, "connector": x.connector,
             "schedule": x.schedule, "options": x.options} for x in s.project.sources],
        "steps": rows,
    }


@tool("read_step", "The script a step runs.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]},
      scope="research")
def _read_step(s: Session, args: dict) -> Any:
    sid = _need(args, "id")
    step = next((x for x in s.project.steps if x.id == sid), None)
    if step is None:
        raise ToolError(f"no step '{sid}'. Steps: {', '.join(x.id for x in s.project.steps)}")
    path = s.root / step.script
    if not path.is_file():
        raise ToolError(f"step '{sid}' points at {step.script}, which is not on disk")
    return {"id": sid, "script": step.script, "language": path.suffix.lstrip("."),
            "source": path.read_text()}


# ---- validate and plan -------------------------------------------------------
@tool("check", "Hold the project against the stage contract. Errors block a run.",
      {"properties": {}},
      scope="full")
def _check(s: Session, args: dict) -> Any:
    from qanat.project import validate

    s.reload()
    rep = validate(s.project, s.root)
    return {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}


@tool("plan", "What would change if the file were applied: adds, drift, orphans.",
      {"properties": {}},
      scope="full")
def _plan(s: Session, args: dict) -> Any:
    from qanat.plan import plan as plan_project

    pl = plan_project(s.project, s.root, s.store)
    return {
        "first_run": pl.first_run, "unchanged": pl.unchanged,
        "changes": [{"action": c.action, "target": c.target, "note": c.note,
                     "details": c.details} for c in pl.changes],
    }


# ---- run ---------------------------------------------------------------------
@tool("run", "Run the graph once, or one job. With as_of, screen that single pass.",
      {"properties": {"job": {"type": "string", "description": "one job id; omit for the whole graph"},
                      "as_of": {"type": "string"}, "seed": {"type": "integer"}}},
      writes=True,
      scope="full")
def _run(s: Session, args: dict) -> Any:
    from qanat.runner import run_all, run_job

    as_of, seed = args.get("as_of") or None, args.get("seed")
    if as_of:
        s.store.open_pit(as_of, s.project.time_columns)
    try:
        if args.get("job"):
            results = [run_job(s.store, s.project, s.root, args["job"], as_of=as_of, seed=seed)]
        else:
            results = run_all(s.store, s.project, s.root, as_of=as_of, seed=seed, sources=not as_of)
    finally:
        if as_of:
            s.store.close_pit()
    return {
        "as_of": as_of,
        "ok": all(r.ok for r in results),
        "results": [{"job": r.job_id, "status": r.status, "rows": r.rows,
                     "wrote": list(r.targets), "error": r.error} for r in results],
    }


@tool("screen",
      "Which companies passed a filter, on one date or on every date in a window. "
      "Returns the list, each company's own forward return, and how many could be "
      "evaluated at all. Writes nothing.",
      {"type": "object",
       "properties": {
           "filters": {"type": "array",
                       "description": "clauses, each {metric, op, value}; or a list of "
                                      "named filters each holding clauses",
                       "items": {"type": "object"}},
           "as_of": {"type": "string", "description": "one date, YYYY-MM-DD"},
           "from": {"type": "string", "description": "start of a window"},
           "to": {"type": "string", "description": "end of a window"},
           "every": {"type": "string", "default": "1y",
                     "description": "gap between dates in a window"},
       },
       "required": ["filters"]},
      "research")
def _screen(s: Session, args: dict) -> Any:
    from qanat.screen import ScreenError, parse_filters, screen

    try:
        return screen(
            s.store, s.project, parse_filters(args.get("filters")),
            as_of=args.get("as_of"), frm=args.get("from"), to=args.get("to"),
            every=args.get("every") or "1y",
        )
    except ScreenError as exc:
        raise ToolError(str(exc)) from exc


@tool("list_metrics",
      "Every metric this project defines: its name, version, unit and formula. "
      "A metric is shared arithmetic; the threshold you compare it against is yours.",
      {"type": "object", "properties": {}}, "data")
def _list_metrics(s: Session, args: dict) -> Any:
    return [{"id": m.id, "version": m.version, "unit": m.unit,
             "description": m.description, "sql": m.sql}
            for m in s.project.metrics]


# ----------------------------------------------------------- the reading surface
def _reading(fn, s, args, **kw):
    from qanat.reading import ReadingError
    try:
        return fn(s.store, s.project, **kw)
    except ReadingError as exc:
        raise ToolError(str(exc)) from exc


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
      "a step is edited -- the rows simply stop being current. Anything reading them is reading "
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


@tool("list_runs", "Recent job runs, with status and row counts.",
      {"properties": {"limit": {"type": "integer", "default": 30}}},
      scope="full")
def _list_runs(s: Session, args: dict) -> Any:
    return {"runs": s.store.recent_runs(int(args.get("limit") or 30))}


# ---- author ------------------------------------------------------------------
@tool("save_step",
      "Add a step, or replace one that exists. `script` is written to disk when `source` is given. "
      "`from` is a list and means it: a step may read several tables, across any stage earlier "
      "than the one it writes.",
      {"properties": {
          "id": {"type": "string"},
          "from": {"type": "array", "items": {"type": "string"},
                   "description": "every table this step may read. `ctx.read()` refuses anything "
                                  "not named here, so this is the permission set as well as the "
                                  "dependency list"},
          "to": {"type": "array", "items": {"type": "string"}},
          "script": {"type": "string", "description": "path relative to the project root"},
          "source": {"type": "string", "description": "the script body to write"},
          "universe": {"type": "string"},
          "schedule": {"type": "string"},
          "options": {"type": "object"},
      }, "required": ["id", "to", "script"]},
      writes=True,
      scope="full")
def _save_step(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_step

    body = args.pop("source", None)
    raw = {k: v for k, v in args.items() if v is not None}
    if body is not None:
        path = s.root / raw["script"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    try:
        written = save_step(s.project, s.root, raw, create_script=body is None)
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"saved": raw["id"], "warnings": written}


@tool("remove_step", "Delete a step from the project file. The script on disk is left alone.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]}, writes=True,
      scope="full")
def _remove_step(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, delete_step

    try:
        written = delete_step(s.project, s.root, _need(args, "id"))
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"removed": args["id"], "warnings": written}


@tool("save_universe",
      "Add or replace a universe: the symbols a portfolio may hold. Pass `symbols` and the csv "
      "is written for you. A step that holds a basket holds itself to one, so a project with "
      "none cannot install one until this has been called.",
      {"properties": {
          "id": {"type": "string", "description": "lower case, e.g. tech8"},
          "index": {"type": "string", "description": "what to call it in front of a person"},
          "symbols": {"type": "array", "items": {"type": "string"},
                      "description": "the symbols it may hold. Written to a csv under the "
                                     "project. Leave out only if the file already exists"},
          "file": {"type": "string",
                   "description": "where the csv lives, relative to the project. Defaults to "
                                  "./universes/<id>.csv"},
      }, "required": ["id"]},
      writes=True,
      scope="full")
def _save_universe(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_universe

    uid = _need(args, "id")
    raw = {
        "id": uid,
        "index": args.get("index") or "",
        "symbols": args.get("file") or f"./universes/{uid}.csv",
    }
    syms = args.get("symbols")
    if syms is not None:
        raw["symbols_list"] = [str(x).strip() for x in syms if str(x).strip()]
    try:
        written = save_universe(s.project, s.root, raw)
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    u = s.project.universe(uid)
    return {"saved": uid, "symbols_file": u.symbols if u else raw["symbols"],
            "symbols": len(syms or []), "warnings": written,
            "note": "a list with no join and leave dates is today's membership applied to the "
                    "past, which flatters every number built on it. Add `from` and `to` columns "
                    "to the csv when you know them"}


@tool("save_source", "Add or replace a source.",
      {"properties": {"id": {"type": "string"},
                      "to": {"type": "array", "items": {"type": "string"}},
                      "connector": {"type": "string", "enum": ["rest", "sql", "csv", "sec", "yfinance"]},
                      "mode": {"type": "string", "enum": ["append", "replace"]},
                      "schedule": {"type": "string"}, "options": {"type": "object"}},
       "required": ["id", "to", "connector"]}, writes=True,
      scope="full")
def _save_source(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_source

    try:
        written = save_source(s.project, s.root, {k: v for k, v in args.items() if v is not None})
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"saved": args["id"], "warnings": written}


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


def serve_stdio(project_path: str | None = None, scope: str = "full") -> int:
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


__all__ = ["PROTOCOL", "SCOPES", "TOOLS", "ScopeError", "Session", "ToolError",
           "rank", "serve_stdio", "tools_for"]
