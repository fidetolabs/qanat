"""The same engine, addressed by an agent instead of by a person.

`qanat serve` gives the console an HTTP API. `qanat mcp` gives an agent the same
service layer over MCP on stdio. Neither one holds logic: both call `store`,
`editor`, `runner`, `plan` and `backtest`, which is the whole point -- if the
console can do it, an agent can do it, and there is no third place where the
behaviour could quietly differ.

Six things this tries to be, because an agent needs them and a person does not:

  * **discoverable** -- `list_tables` and `describe_table` mean no documentation
    is required to find out what exists
  * **addressable** -- every table, job, run and backtest has an id
  * **structured** -- results are JSON with numbers in them, never a screenshot
  * **repeatable** -- `backtest` takes a seed, and the same seed is the same answer
  * **honest in failure** -- an error says what to do next, not only what broke
  * **read-only on request** -- `--read-only` removes every tool that writes

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
        #: Set once `open_console` has served the console from this same session,
        #: so the page a person is watching and the tools an agent is calling are
        #: one process over one store, and cannot disagree.
        self.console: dict[str, Any] | None = None

    @property
    def store(self):
        from qanat.store import Store

        if self._store is None:
            self._store = Store(self.project.store_url(self.root))
        return self._store

    def reload(self) -> None:
        from qanat.project import load

        self.project, self.root = load(self.path)
        if self.console:
            # the open page is reading AppState, so hand it the reloaded file too
            self.console["state"].set_project(self.project)

    def close(self) -> None:
        if self.console and self.console.get("server") is not None:
            self.console["server"].should_exit = True
            self.console = None
        if self._store is not None:
            self._store.close()
            self._store = None


# ----------------------------------------------------------------------- tools
TOOLS: list[dict[str, Any]] = []


def tool(name: str, description: str, schema: dict[str, Any], writes: bool = False):
    def wrap(fn: Callable[[Session, dict], Any]):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": {"type": "object", "properties": schema.get("properties", {}),
                            "required": schema.get("required", [])},
            "handler": fn,
            "writes": writes,
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
      {"properties": {}})
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
      {"properties": {"ref": {"type": "string", "description": "stage.table"}}, "required": ["ref"]})
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
       "required": ["ref"]})
def _sample_table(s: Session, args: dict) -> Any:
    ref = _known_table(s, _need(args, "ref"))
    limit = int(args.get("limit") or 20)
    df = s.store.read(ref, limit=limit, as_of=args.get("as_of") or None)
    return {"ref": ref, "as_of": args.get("as_of"), "rows": len(df),
            "sample": json.loads(df.to_json(orient="records", date_format="iso"))}


@tool("lineage", "What feeds a table, and which alphas break if it breaks.",
      {"properties": {"ref": {"type": "string"}}, "required": ["ref"]})
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
    wl = s.project.weights_stage
    return {
        "ref": ref, "upstream": upstream, "downstream": downstream,
        "reaches": reach,
        "breaks_portfolio": bool(wl) and any(r.startswith(f"{wl.id}.") for r in reach),
    }


@tool("list_steps", "Every job in the graph -- sources and steps -- in dependency order.",
      {"properties": {}})
def _list_steps(s: Session, args: dict) -> Any:
    from qanat.runner import order

    return {
        "sources": [{"id": x.id, "to": x.writes, "connector": x.connector,
                     "schedule": x.schedule, "options": x.options} for x in s.project.sources],
        "steps": [{"id": x.id, "from": x.reads, "to": x.writes, "script": x.script,
                   "universe": x.universe, "schedule": x.schedule, "options": x.options}
                  for x in order(s.project)],
    }


@tool("read_step", "The script a step runs.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]})
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
      {"properties": {}})
def _check(s: Session, args: dict) -> Any:
    from qanat.project import validate

    s.reload()
    rep = validate(s.project, s.root)
    return {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}


@tool("plan", "What would change if the file were applied: adds, drift, orphans.",
      {"properties": {}})
def _plan(s: Session, args: dict) -> Any:
    from qanat.plan import plan as plan_project

    pl = plan_project(s.project, s.root, s.store)
    return {
        "first_run": pl.first_run, "unchanged": pl.unchanged,
        "changes": [{"action": c.action, "target": c.target, "note": c.note,
                     "details": c.details} for c in pl.changes],
    }


# ---- run ---------------------------------------------------------------------
@tool("run", "Run the graph once, or one job. With as_of, replay that single pass.",
      {"properties": {"job": {"type": "string", "description": "one job id; omit for the whole graph"},
                      "as_of": {"type": "string"}, "seed": {"type": "integer"}}},
      writes=True)
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


@tool("backtest_conditions",
      "What has to be decided before a backtest means anything: the window, the universe, "
      "the rebalance gap and the decay. Call this FIRST, show the person the choices, and "
      "ask them -- these change the answer, so they are not yours to assume.",
      {"properties": {}})
def _backtest_conditions(s: Session, args: dict) -> Any:
    bt = s.project.backtest
    if bt is None:
        raise ToolError(
            "this project has no `backtest:` block, so there is nothing to price weights with. "
            "Add one naming a prices table, or copy the block from `qanat init`."
        )
    span = {}
    if s.store.exists(bt.prices):
        col = s.store.time_column(bt.prices, s.project.time_columns.get(bt.prices))
        if col:
            df = s.store.read(bt.prices)
            span = {"earliest": str(df[col].min()), "latest": str(df[col].max())}

    universes = [{"id": u.id, "index": u.index,
                  "symbols_file": u.symbols} for u in s.project.universes]
    on_steps = sorted({st.universe for st in s.project.steps if st.universe})
    return {
        "ask_the_person_for": ["alpha", "from", "to", "universe", "rebalance", "decay", "split"],
        "alphas_in_this_project": [s.project.alpha_name(a, ref) for a, ref in s.project.alphas],
        "alpha": {"note": "one name, or several to price them as one portfolio. Ask which. A "
                          "combined book is a different strategy from either alpha alone, and it "
                          "is usually the better one, so it is worth offering",
                  "allocation": "share per alpha, equal unless the person says otherwise"},
        "why": "each one changes the number. A backtest run on conditions nobody chose is a guess "
               "with a decimal point on it.",
        "window": {"data_available": span or "prices table not written yet",
                   "note": "from and to are as-of dates, both ends included"},
        "universe": {
            "declared_on_steps": on_steps,
            "available": universes,
            "note": "omit to use what each step already declares; name one to hold the same alpha "
                    "to a different set of symbols",
        },
        "rebalance": {"default": bt.rebalance,
                      "note": "gap between as-of dates, e.g. 1d, 5d, 20d. Shorter means more "
                              "decisions and more turnover"},
        "split": {"default": bt.split or None,
                  "note": "first out-of-sample date. Everything before it is in-sample: the half "
                          "you were allowed to look at while choosing the settings. Report both, "
                          "and believe the out-of-sample one"},
        "decay": {"default": bt.decay,
                  "note": "hold a blend of the last N portfolios, newest heaviest. 0 or 1 is off. "
                          "Higher cuts turnover, and therefore cost, at the price of reacting later"},
        "costs_are_fixed_in_the_file": {"fee_bps": bt.fee_bps, "slippage_bps": bt.slippage_bps,
                                        "purge": bt.purge, "embargo": bt.embargo},
        "seed": {"default": 0, "note": "same seed, same answer"},
    }


@tool("backtest",
      "Replay the pipeline across a window and price what it held. Returns net edge after fees "
      "and slippage. Same seed and same project means the same numbers. Call backtest_conditions "
      "first and ask the person for the window, universe, rebalance and decay -- do not pick "
      "them silently, because each one changes the answer.",
      {"properties": {"from": {"type": "string", "description": "first as-of date"},
                      "to": {"type": "string", "description": "last as-of date"},
                      "rebalance": {"type": "string", "description": "e.g. 1d, 5d, 20d"},
                      "universe": {"type": "string",
                                   "description": "hold the alpha to this universe instead of "
                                                  "the one the step declares"},
                      "decay": {"type": "integer",
                                "description": "blend the last N portfolios; 0 or 1 is off"},
                      "alpha": {"type": ["string", "array"], "items": {"type": "string"},
                                "description": "which alpha to price. Required once the project "
                                               "has more than one. Give several to price them as "
                                               "one portfolio: each keeps its own weights table "
                                               "and the run holds the sum"},
                      "allocation": {"type": "object",
                                     "description": "share of the money per alpha, e.g. "
                                                    "{\"momentum\": 3, \"low_vol\": 1}. "
                                                    "Equal split if left out"},
                      "split": {"type": "string",
                                "description": "first out-of-sample date; before it is in-sample"},
                      "fee_bps": {"type": "number",
                                  "description": "commission on turnover, in basis points. "
                                                 "Defaults to the project's. Re-run with a "
                                                 "higher one to find where the edge dies"},
                      "slippage_bps": {"type": "number",
                                       "description": "slippage on turnover, in basis points"},
                      "purge": {"type": "string",
                                "description": "hold rows back this long before a step may read "
                                               "them, e.g. 1d"},
                      "embargo": {"type": "string",
                                  "description": "wait this long after the as-of date before a "
                                                 "return counts, e.g. 1d"},
                      "seed": {"type": "integer", "default": 0}},
       "required": ["from", "to"]},
      writes=True)
def _backtest(s: Session, args: dict) -> Any:
    from qanat.backtest import BacktestError, run_backtest

    decay = args.get("decay")
    try:
        res = run_backtest(
            s.store, s.project, s.root, _need(args, "from"), _need(args, "to"),
            rebalance=args.get("rebalance") or None, seed=int(args.get("seed") or 0),
            decay=None if decay is None else int(decay),
            universe=args.get("universe") or None,
            split=args.get("split") or None, alpha=args.get("alpha") or None,
            allocation=args.get("allocation") or None,
            fee_bps=args.get("fee_bps"), slippage_bps=args.get("slippage_bps"),
            purge=args.get("purge") or None, embargo=args.get("embargo") or None,
        )
    except BacktestError as exc:
        raise ToolError(str(exc)) from exc
    d = res.as_dict()
    d["periods"] = d["periods"][:200]
    return d


@tool("stale_tables",
      "Tables whose rows were computed by a job that has changed since. Nothing is deleted when "
      "a step is edited -- the rows simply stop being current. Anything reading them is reading "
      "yesterday's answer until the pipeline is run again.",
      {"properties": {}, "required": []})
def _stale_tables(s: Session, args: dict) -> Any:
    from qanat.plan import plan as plan_project

    pl = plan_project(s.project, s.root, s.store)
    stale = sorted(pl.stale(s.project, s.store))
    return {
        "stale": stale,
        "why": [{"job": c.target, "what_moved": c.details or [c.note]} for c in pl.updates],
        "fix": "run. The tables are rebuilt from the raw rows, which a replay never touches",
    } if stale else {"stale": [], "fix": "nothing to do: every table matches the file"}


@tool("alpha_book",
      "Every alpha this project has already backtested, and how each one did. Show this when "
      "someone asks what has been tried, or wants to go back to one.",
      {"properties": {}})
def _alpha_book(s: Session, args: dict) -> Any:
    book = s.store.alpha_book()
    wl = s.project.weights_stage
    wired = next((st.id for st in s.project.steps
                  if wl and any(w.startswith(f"{wl.id}.") for w in st.writes)), None)
    for row in book:
        row["wired"] = row["alpha"] == wired
    for row in book:
        found = s.project.alpha(row["alpha"])
        row["name"] = s.project.alpha_name(row["alpha"], found[1] if found else "")
    return {"wired_now": wired, "alphas": book,
            "note": "use_alpha or save_step changes which one writes the weights table"}


@tool("list_backtests", "Every replay this project has run, newest first.",
      {"properties": {"limit": {"type": "integer", "default": 25}}})
def _list_backtests(s: Session, args: dict) -> Any:
    return {"backtests": s.store.backtests(int(args.get("limit") or 25))}


@tool("report", "One backtest in full: totals and every period.",
      {"properties": {"run_id": {"type": "integer"}}, "required": ["run_id"]})
def _report(s: Session, args: dict) -> Any:
    row = s.store.backtest(int(_need(args, "run_id")))
    if row is None:
        ids = [b["run_id"] for b in s.store.backtests(10)]
        raise ToolError(f"no backtest {args['run_id']}. Recent runs: {ids or '(none yet)'}")
    if row.get("report"):
        return json.loads(row["report"])
    return row


@tool("period",
      "One point on the curve, opened up: what was held at that rebalance, what each name "
      "returned over the period, what was traded to get there, and whether it was in-sample.",
      {"properties": {"run_id": {"type": "integer"}, "as_of": {"type": "string"}},
       "required": ["run_id", "as_of"]})
def _period(s: Session, args: dict) -> Any:
    from qanat.backtest import BacktestError, period_detail

    try:
        return period_detail(s.store, s.project, int(_need(args, "run_id")), _need(args, "as_of"))
    except BacktestError as exc:
        raise ToolError(str(exc)) from exc


@tool("weights", "The portfolio a backtest held, for one as-of date or all of them.",
      {"properties": {"run_id": {"type": "integer"}, "as_of": {"type": "string"}},
       "required": ["run_id"]})
def _weights(s: Session, args: dict) -> Any:
    run_id = int(_need(args, "run_id"))
    rows = s.store.bt_weights(run_id, args.get("as_of") or None)
    if not rows:
        raise ToolError(
            f"backtest {run_id} recorded no weights"
            + (f" at {args['as_of']}" if args.get("as_of") else "")
            + ". Call report to see whether its periods failed."
        )
    return {"run_id": run_id, "count": len(rows), "weights": rows}


@tool("compare", "What moved between two backtests, and whether they asked the same question.",
      {"properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]})
def _compare(s: Session, args: dict) -> Any:
    from qanat.backtest import compare

    a, b = s.store.backtest(int(_need(args, "a"))), s.store.backtest(int(_need(args, "b")))
    for rid, row in ((args["a"], a), (args["b"], b)):
        if row is None:
            raise ToolError(f"no backtest {rid}")
    return compare(a, b)


@tool("list_runs", "Recent job runs, with status and row counts.",
      {"properties": {"limit": {"type": "integer", "default": 30}}})
def _list_runs(s: Session, args: dict) -> Any:
    return {"runs": s.store.recent_runs(int(args.get("limit") or 30))}


# ---- the shelf ---------------------------------------------------------------
@tool("list_alphas",
      "The alphas that ship with Qanat, ready to wire up and replay. Show these to the person "
      "and let them pick one -- then call backtest_conditions before running anything.",
      {"properties": {}})
def _list_alphas(s: Session, args: dict) -> Any:
    from qanat import alphas

    return {
        "alphas": alphas.describe(),
        "in_this_project": [{"id": a, "writes": ref} for a, ref in s.project.alphas] or None,
        "note": "each alpha has its own weights table and its own path through the graph, so "
                "adding one leaves the others alone.",
    }


@tool("read_alpha", "The script an alpha on the shelf would install, before installing it.",
      {"properties": {"name": {"type": "string"}}, "required": ["name"]})
def _read_alpha(s: Session, args: dict) -> Any:
    from qanat import alphas

    name = _need(args, "name")
    try:
        return {"name": name, "source": alphas.script_for(name)}
    except KeyError as exc:
        raise ToolError(str(exc)) from exc


@tool("use_alpha",
      "Install one of the shelf alphas as the step that writes the weights table. Ask the person "
      "which alpha and which universe first; `reads` should be a table with a symbol, a date and "
      "a price -- call list_tables if you are not sure which one that is.",
      {"properties": {
          "name": {"type": "string", "description": "an alpha from list_alphas"},
          "reads": {"type": "string", "description": "stage.table holding symbol, date, price"},
          "universe": {"type": "string"},
          "options": {"type": "object", "description": "overrides, e.g. {\"lookback\": 120}"},
      }, "required": ["name", "reads"]},
      writes=True)
def _use_alpha(s: Session, args: dict) -> Any:
    from qanat import alphas
    from qanat.editor import EditorError, save_step

    name = _need(args, "name")
    if name not in alphas.CATALOGUE:
        raise ToolError(f"no alpha called '{name}'. On the shelf: {', '.join(alphas.CATALOGUE)}")
    reads = _known_table(s, _need(args, "reads"))

    wl = s.project.weights_stage
    if wl is None:
        raise ToolError("this project has no weights stage, so there is nowhere to put a portfolio")
    # One table per alpha: adding one does not take the last one's place.
    step_id = f"alpha_{name}"
    target = f"{wl.id}.{name}"

    universe = args.get("universe")
    if universe and s.project.universe(universe) is None:
        known = ", ".join(u.id for u in s.project.universes) or "(none defined)"
        raise ToolError(f"unknown universe '{universe}'. This project has: {known}")
    if not universe:
        universe = next((st.universe for st in s.project.steps if st.universe), None)
    if not universe:
        raise ToolError(
            "every shelf alpha holds itself to a universe, and this project declares none. "
            "Add one to qanat.yaml (id + a csv with a `symbol` column) and pass it here."
        )

    try:
        script = alphas.write_alpha(s.root, name)
        opts = dict(alphas.CATALOGUE[name]["options"])
        opts.update(args.get("options") or {})
        opts["reads"] = reads
        written = save_step(s.project, s.root, {
            "id": step_id, "from": [reads], "to": [target],
            "script": script, "universe": universe, "options": opts,
        }, create_script=False)
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {
        "installed": step_id, "script": script, "reads": reads, "writes": target,
        "universe": universe, "options": opts, "files": written,
        "book": [a for a, _ in s.project.alphas],
        "next": "run, then backtest_conditions, then backtest with alpha=" + step_id,
    }


# ---- the console -------------------------------------------------------------
def _free_port(host: str, first: int) -> int:
    import socket

    for port in range(first, first + 40):
        with socket.socket() as s:
            if s.connect_ex((host, port)) != 0:
                return port
    raise ToolError(f"no free port between {first} and {first + 40}")


@tool("open_console",
      "Open the Qanat console in the person's browser so they can watch the DAG and the "
      "backtest charts while you work. Serves from this same session, so anything you run "
      "shows up on the page. Call it once; calling it again returns the same URL. "
      "Use view='backtests' to land them on the charts.",
      {"properties": {
          "view": {"type": "string", "enum": ["pipeline", "backtests"], "default": "pipeline"},
          "port": {"type": "integer", "default": 8420},
          "browser": {"type": "boolean", "default": True,
                      "description": "false serves it but does not open a window"},
      }},
      writes=True)
def _open_console(s: Session, args: dict) -> Any:
    import threading
    import time
    import webbrowser

    view = args.get("view") or "pipeline"
    suffix = "?view=backtests" if view == "backtests" else ""

    if s.console:
        url = s.console["url"] + suffix
        if args.get("browser", True):
            webbrowser.open(url)
        return {"url": url, "already_open": True,
                "note": "the console was already serving; the same page was brought forward"}

    import uvicorn

    from qanat.api import AppState, create_app

    host = "127.0.0.1"
    port = _free_port(host, int(args.get("port") or 8420))
    # No scheduler: in this session the agent decides when something runs, not cron.
    state = AppState(store=s.store, project=s.project, root=s.root, sched=None)
    server = uvicorn.Server(uvicorn.Config(create_app(state), host=host, port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()

    deadline = time.time() + 10
    while not getattr(server, "started", False) and time.time() < deadline:
        time.sleep(0.05)
    if not getattr(server, "started", False):
        raise ToolError(f"the console did not come up on port {port} within 10s")

    base = f"http://{host}:{port}"
    s.console = {"url": base, "server": server, "state": state}
    if args.get("browser", True):
        webbrowser.open(base + suffix)
    return {
        "url": base + suffix,
        "already_open": False,
        "shows": ["the DAG, with live row counts and job status",
                  "the backtest panel: equity curve, per-period net, and the cost breakdown"],
        "note": "The page polls, so a run or a backtest you start now appears without a reload.",
    }


@tool("console_status", "Whether the console is being served from this session, and where.",
      {"properties": {}})
def _console_status(s: Session, args: dict) -> Any:
    if not s.console:
        return {"open": False, "note": "call open_console to serve it"}
    return {"open": True, "url": s.console["url"]}


# ---- author ------------------------------------------------------------------
@tool("save_step",
      "Add a step, or replace one that exists. `script` is written to disk when `source` is given.",
      {"properties": {
          "id": {"type": "string"},
          "from": {"type": "array", "items": {"type": "string"}},
          "to": {"type": "array", "items": {"type": "string"}},
          "script": {"type": "string", "description": "path relative to the project root"},
          "source": {"type": "string", "description": "the script body to write"},
          "universe": {"type": "string"},
          "schedule": {"type": "string"},
          "options": {"type": "object"},
      }, "required": ["id", "to", "script"]},
      writes=True)
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
    return {"saved": raw["id"], "files": written}


@tool("remove_step", "Delete a step from the project file. The script on disk is left alone.",
      {"properties": {"id": {"type": "string"}}, "required": ["id"]}, writes=True)
def _remove_step(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, delete_step

    try:
        written = delete_step(s.project, s.root, _need(args, "id"))
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"removed": args["id"], "files": written}


@tool("save_source", "Add or replace a source.",
      {"properties": {"id": {"type": "string"},
                      "to": {"type": "array", "items": {"type": "string"}},
                      "connector": {"type": "string", "enum": ["rest", "sql", "csv", "synthetic"]},
                      "mode": {"type": "string", "enum": ["append", "replace"]},
                      "schedule": {"type": "string"}, "options": {"type": "object"}},
       "required": ["id", "to", "connector"]}, writes=True)
def _save_source(s: Session, args: dict) -> Any:
    from qanat.editor import EditorError, save_source

    try:
        written = save_source(s.project, s.root, {k: v for k, v in args.items() if v is not None})
    except EditorError as exc:
        raise ToolError(str(exc)) from exc
    s.reload()
    return {"saved": args["id"], "files": written}


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


def serve_stdio(project_path: str | None = None, read_only: bool = False) -> int:
    """Speak MCP on stdin/stdout until the client goes away."""
    try:
        session = Session(project_path)
        _ = session.store  # open now, so a busy database is reported before a client connects
    except Exception as exc:  # noqa: BLE001
        print(f"qanat mcp: {exc}", file=sys.stderr)
        return 1

    tools = [t for t in TOOLS if not (read_only and t["writes"])]
    print(f"qanat mcp: {session.project.name} · {len(tools)} tools"
          f"{' (read-only)' if read_only else ''}", file=sys.stderr)

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


__all__ = ["PROTOCOL", "TOOLS", "Session", "ToolError", "serve_stdio"]
