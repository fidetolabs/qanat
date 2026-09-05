"""The read model the console draws, and the buttons it can press.

Everything here is derived from the store. The API invents nothing: if a table
says 12,043 rows, that is a count(*), and if a node is red, a run row says so.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AliasChoices, BaseModel, Field

from qanat import __version__
from qanat.editor import (
    EditorError,
    add_stage,
    delete_source,
    delete_stage,
    delete_step,
    drop_table,
    replace_project,
    save_source,
    save_step,
    set_retention,
    set_store,
)
from qanat.models import Project, Step
from qanat.plan import plan as plan_project
from qanat.project import edges as graph_edges
from qanat.project import load, validate
from qanat.project_io import dump_project
from qanat.retention import run_retention
from qanat.runner import order as run_order
from qanat.scheduler import Scheduler
from qanat.store import Store

CONSOLE = Path(__file__).parent / "console"


class AlphaRequest(BaseModel):
    """What an alpha needs to exist: a name, a table to read, and a rule."""

    # The step to change. An alpha added here is called `alpha_<name>`, but one
    # written by hand can be called anything -- the scaffold's own is `portfolio`
    # writing `weights.target`. Editing it has to change that step, not invent a
    # second one beside it.
    id: str | None = None
    name: str
    reads: str
    universe: str | None = None
    shelf: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    # how this alpha wants to be run, not what its script computes
    rebalance: str | None = None
    decay: int | None = None


class BacktestRequest(BaseModel):
    """A replay window. `frm` is spelled that way because `from` is a keyword."""

    frm: str = Field(validation_alias=AliasChoices("from", "frm"))
    to: str
    rebalance: str | None = None
    seed: int = 0
    decay: int | None = None
    universe: str | None = None
    split: str | None = None
    # one alpha, or several priced as one portfolio
    alpha: str | list[str] | None = None
    allocation: dict[str, float] | None = None
    # costs are a condition of the run, not of the file: "where does this edge die?"
    fee_bps: float | None = None
    slippage_bps: float | None = None
    purge: str | None = None
    embargo: str | None = None


def _mask(text: str) -> str:
    """A DSN without its password. Nothing here should ever hand one back."""
    import re

    return re.sub(r"(://[^:/@]+):[^@]*@", r"\1:***@", str(text))


def _connection_of(job: Any, root: Path) -> dict[str, Any]:
    """Where a source's rows come from.

    A raw table has no step behind it -- it was brought in. So this answers the
    question the panel would otherwise leave blank: which file, which endpoint,
    which server. `${VARS}` are left unexpanded and passwords are masked, because
    the console is a place to read a pipeline, not to read a secret out of one.
    """
    o = dict(job.options or {})
    conn: dict[str, Any] = {"connector": job.connector, "mode": job.mode}
    if job.connector == "csv":
        path = o.get("path") or o.get("file") or ""
        conn["kind"] = "a file on this machine"
        conn["path"] = str(root / path) if path and not str(path).startswith("/") else str(path)
        conn["exists"] = Path(conn["path"]).is_file() if conn["path"] else False
    elif job.connector == "rest":
        conn["kind"] = "an HTTP endpoint"
        conn["url"] = o.get("url")
        conn["params"] = o.get("params") or {}
        conn["headers"] = sorted((o.get("headers") or {}).keys())
        conn["records"] = o.get("records")
    elif job.connector == "sql":
        conn["kind"] = "a database"
        conn["dsn"] = _mask(o.get("dsn") or o.get("url") or "")
        conn["query"] = o.get("query") or o.get("table")
    else:
        conn["kind"] = "generated here. No network, no keys"
        conn["options"] = o
    return conn


def _book_stats(store: Store, book: list[dict[str, Any]]) -> dict[str, Any]:
    """How the book hangs together, and whether its alphas are the same bet.

    Correlation is the question a second alpha has to answer. Two rules that both
    made money are one rule twice if they made it on the same days, and adding the
    second one buys nothing but its own costs. The series compared are each alpha's
    net per rebalance, aligned on the dates they share.
    """
    # A blend is made of the alphas beside it, so of course it correlates with them.
    # The question this matrix answers is whether two *alphas* are the same bet, and
    # putting a blend in it would answer a question nobody asked.
    scored = [b for b in book if b.get("last_run_id") and "+" not in (b.get("alpha") or "")]
    series: dict[str, dict[str, float]] = {}
    for b in scored:
        rows = store.bt_periods(b["last_run_id"])
        if rows:
            series[b["alpha"]] = {str(r["as_of"]): float(r["net"]) for r in rows}

    pairs = []
    names = sorted(series)
    for i, a in enumerate(names):
        for b_ in names[i + 1:]:
            shared = sorted(set(series[a]) & set(series[b_]))
            if len(shared) < 4:
                pairs.append({"a": a, "b": b_, "r": None, "n": len(shared),
                              "why": "too few shared dates to say"})
                continue
            xs = [series[a][d] for d in shared]
            ys = [series[b_][d] for d in shared]
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
            vx = math.sqrt(sum((x - mx) ** 2 for x in xs))
            vy = math.sqrt(sum((y - my) ** 2 for y in ys))
            pairs.append({"a": a, "b": b_, "n": len(shared),
                          "r": None if vx == 0 or vy == 0 else cov / (vx * vy)})

    best = [b["out_of_sample"] for b in book if b.get("out_of_sample") is not None]
    return {
        "alphas": sum(1 for b in book if "+" not in (b.get("alpha") or "")),
        "blends": sum(1 for b in book if "+" in (b.get("alpha") or "")),
        "tested": len(scored),
        "runs": sum(b.get("runs") or 0 for b in book),
        "best_out_of_sample": max(best) if best else None,
        "correlation": pairs,
        "note": ("one alpha is not a book. Add a second and this says whether it is a "
                 "different bet or the same one twice" if len(names) < 2 else None),
    }


@dataclass
class AppState:
    store: Store
    project: Project
    root: Path
    sched: Scheduler | None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    #: Held for the whole of a replay, and by nothing else. The state lock guards
    #: edits to the project, which a replay never makes -- so a backtest must not
    #: take it, or the console would go blank for exactly as long as the run that
    #: someone opened the console to watch.
    _replay: threading.Lock = field(default_factory=threading.Lock)

    def reload(self) -> None:
        with self._lock:
            self.project, self.root = load(self.root)
            if self.sched:
                self.sched.reload(self.project)

    def set_project(self, project: Project) -> None:
        with self._lock:
            self.project = project
            if self.sched:
                self.sched.reload(project)


def _status_of(last: dict[str, Any] | None, running: bool) -> str:
    if running:
        return "running"
    if last is None:
        return "idle"
    return {"ok": "ok", "failed": "failed", "running": "running"}.get(last.get("status"), "idle")


def build_graph(
    store: Store, project: Project, root: Path, sched: Scheduler | None
) -> dict[str, Any]:
    last = store.last_run_by_job()
    sched_state = sched.status() if sched else {}
    producers = project.producers()

    jobs = []
    for j in project.jobs:
        st = sched_state.get(j.id, {})
        lr = last.get(j.id)
        jobs.append({
            "id": j.id,
            "kind": "source" if j in project.sources else "step",
            "connector": getattr(j, "connector", None),
            "script": getattr(j, "script", None),
            "from": list(j.reads),
            "to": list(j.writes),
            "universe": getattr(j, "universe", None),
            "schedule": j.schedule,
            "when": list(getattr(j, "when", [])),
            "mode": getattr(j, "mode", None),
            "options": getattr(j, "options", {}),
            "next_at": st.get("next_at"),
            "status": _status_of(lr, bool(st.get("running"))),
            "last_run": lr,
        })
    by_id = {j["id"]: j for j in jobs}

    tables = []
    for ref in project.tables():
        info = store.table_info(ref)
        stage_id = ref.split(".")[0]
        lay = project.stage(stage_id)
        owner = producers.get(ref)
        tables.append({
            "ref": ref,
            "stage": stage_id,
            "stage_kind": lay.kind if lay else "features",
            "name": ref.partition(".")[2],
            "producer": owner,
            "producer_kind": by_id.get(owner, {}).get("kind"),
            "producer_connector": by_id.get(owner, {}).get("connector"),
            "rows": info.rows if info else 0,
            "columns": [{"name": c, "type": t} for c, t in (info.columns if info else [])],
            "updated_at": info.updated_at if info else None,
            "status": by_id.get(owner, {}).get("status", "idle"),
            "retention": project.retention.get(ref),
        })

    pl = plan_project(project, root, store)
    for ch in pl.orphans:
        info = store.table_info(ch.target)
        stage_id = ch.target.split(".")[0]
        lay = project.stage(stage_id)
        tables.append({
            "ref": ch.target,
            "stage": stage_id,
            "stage_kind": lay.kind if lay else "features",
            "name": ch.target.partition(".")[2],
            "producer": None,
            "producer_kind": None,
            "producer_connector": None,
            "rows": info.rows if info else 0,
            "columns": [{"name": c, "type": t} for c, t in (info.columns if info else [])],
            "updated_at": info.updated_at if info else None,
            "status": "orphan",
            "retention": project.retention.get(ch.target),
        })

    # Which tables were computed by something that has since changed. Nothing is
    # deleted -- the rows just stop claiming to be current.
    stale = pl.stale(project, store)
    for t in tables:
        t["stale"] = t["ref"] in stale

    ok = sum(1 for t in tables if t["status"] == "ok")
    bad = sum(1 for t in tables if t["status"] == "failed")
    pnl_stage = project.pnl_stage
    if pnl_stage is not None:
        from qanat.backtest import alpha_ids_of, pnl_ref

        # One PnL table per alpha, plus one for every blend that has actually been
        # run: a blend is not declared anywhere, it exists because somebody priced
        # two alphas together, so history is the only place it can be read from.
        keys = [a for a, _ in project.alphas]
        declared = set(keys)
        for row in store.alpha_book():
            k = row.get("alpha") or ""
            if "+" in k and k not in declared and all(i in declared for i in alpha_ids_of(k)):
                keys.append(k)

        seen = {t["ref"] for t in tables}
        for key in keys:
            ref = pnl_ref(project, key)
            if ref is None:
                continue
            makers = alpha_ids_of(key)
            if ref in seen:
                # already listed (the store has it as an orphan); name its maker
                for t in tables:
                    if t["ref"] == ref:
                        t.update(producer=makers[0], producers=makers, producer_kind="replay",
                                 stage_kind="pnl", status="ok", written_by_replay=True)
                continue
            info = store.table_info(ref)
            tables.append({
                "ref": ref, "stage": pnl_stage.id, "stage_kind": "pnl",
                "name": ref.partition(".")[2], "producer": makers[0], "producers": makers,
                "producer_kind": "replay", "producer_connector": None,
                "rows": info.rows if info else 0,
                "columns": ([{"name": c, "type": t} for c, t in info.columns] if info else []),
                "updated_at": info.updated_at if info else None,
                "status": "ok" if info else "idle", "written_by_replay": True,
            })

    return {
        "project": project.name,
        "version": __version__,
        "store": project.store,
        "stages": [
            {"id": x.id, "kind": x.kind, "description": x.description,
             "tables": [t["ref"] for t in tables if t["stage"] == x.id]}
            for x in project.stages
        ],
        "universes": [{"id": b.id, "index": b.index, "symbols": b.symbols} for b in project.universes],
        "retention": dict(project.retention),
        "tables": tables,
        "jobs": jobs,
        "edges": [{"from": a, "to": b, "step": s} for a, b, s in graph_edges(project)],
        # the order jobs actually run in. The console draws the pass in this order
        # rather than guessing from the shape, so the lights are the run, not a
        # picture of it
        "run_order": [j.id for j in run_order(project)],
        "health": {
            "tables": len(tables),
            "healthy": ok,
            "failing": bad,
            "rows": sum(t["rows"] for t in tables),
            "scheduled": len(sched_state),
            "drift": len(pl.changes),
        },
        "plan": {
            "changes": [
                {"action": ch.action, "target": ch.target, "note": ch.note,
                 "details": ch.details}
                for ch in pl.changes
            ],
            "unchanged": pl.unchanged,
        },
    }


class StageIn(BaseModel):
    id: str
    kind: str = "features"
    description: str = ""
    before: str | None = None


class RetentionIn(BaseModel):
    retention: dict[str, str]


class StoreIn(BaseModel):
    store: str


class DropIn(BaseModel):
    force: bool = False


def create_app(state: AppState) -> FastAPI:
    app = FastAPI(
        title=f"qanat · {state.project.name}",
        version=__version__,
        docs_url="/api/docs",
    )

    def _editor_error(exc: Exception) -> HTTPException:
        if isinstance(exc, EditorError):
            return HTTPException(400, str(exc))
        return HTTPException(400, f"{type(exc).__name__}: {exc}")

    @app.get("/api/graph")
    def graph() -> dict[str, Any]:
        """The pipeline: its stages, tables, jobs and edges."""
        with state._lock:
            return build_graph(state.store, state.project, state.root, state.sched)

    @app.get("/api/project")
    def project_get() -> dict[str, Any]:
        with state._lock:
            rep = validate(state.project, state.root)
            return {
                "config": dump_project(state.project),
                "valid": rep.ok,
                "errors": rep.errors,
                "warnings": rep.warnings,
            }

    @app.put("/api/project")
    def project_put(body: dict[str, Any]) -> dict[str, Any]:
        try:
            with state._lock:
                project, warnings = replace_project(state.root, body)
                state.set_project(project)
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.put("/api/store")
    def store_put(body: StoreIn) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = set_store(state.project, state.root, body.store)
                state.reload()
                return {"ok": True, "store": body.store, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.post("/api/stages")
    def stage_add(body: StageIn) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = add_stage(
                    state.project, state.root, body.id, body.kind, body.description, body.before
                )
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.delete("/api/stages/{stage_id}")
    def stage_delete(stage_id: str) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = delete_stage(state.project, state.root, stage_id)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.post("/api/sources")
    def source_save(body: dict[str, Any]) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = save_source(state.project, state.root, body)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.delete("/api/sources/{source_id}")
    def source_delete(source_id: str) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = delete_source(state.project, state.root, source_id)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.post("/api/steps")
    def step_save(body: dict[str, Any]) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = save_step(state.project, state.root, body, create_script=True)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.delete("/api/steps/{step_id}")
    def step_delete(step_id: str) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = delete_step(state.project, state.root, step_id)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.put("/api/retention")
    def retention_put(body: RetentionIn) -> dict[str, Any]:
        try:
            with state._lock:
                warnings = set_retention(state.project, state.root, body.retention)
                state.reload()
                return {"ok": True, "warnings": warnings}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.post("/api/retention/run")
    def retention_run_now() -> dict[str, Any]:
        with state._lock:
            removed = run_retention(state.store, state.project)
            return {"ok": True, "removed": removed}

    @app.post("/api/prune")
    def prune_all() -> dict[str, Any]:
        with state._lock:
            pl = plan_project(state.project, state.root, state.store)
            dropped = []
            for ch in pl.orphans:
                rows = state.store.drop(ch.target)
                state.store.event("warn", "prune", f"dropped {ch.target} ({rows:,} rows)")
                dropped.append({"ref": ch.target, "rows": rows})
            if pl.removes:
                state.store.forget_state([ch.target for ch in pl.removes])
            return {"ok": True, "dropped": dropped}

    @app.delete("/api/tables/{stage}/{name}")
    def table_delete(stage: str, name: str, force: bool = False) -> dict[str, Any]:
        ref = f"{stage}.{name}"
        try:
            with state._lock:
                rows = drop_table(state.store, state.project, state.root, ref, force=force)
                state.reload()
                return {"ok": True, "ref": ref, "rows": rows}
        except Exception as exc:
            raise _editor_error(exc) from exc

    @app.get("/api/plan")
    def plan_now() -> dict[str, Any]:
        with state._lock:
            pl = plan_project(state.project, state.root, state.store)
            return {
                "changes": [
                    {"action": c.action, "target": c.target, "note": c.note, "details": c.details}
                    for c in pl.changes
                ],
                "unchanged": pl.unchanged,
                "first_run": pl.first_run,
            }

    @app.get("/api/check")
    def check_now() -> dict[str, Any]:
        """The same contract check the CLI runs. Here so nothing is CLI-only."""
        with state._lock:
            rep = validate(state.project, state.root)
            return {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}

    @app.get("/api/backtest/conditions")
    def backtest_conditions() -> dict[str, Any]:
        """What has to be decided before a backtest means anything.

        The same answer the agent gets from `backtest_conditions`, so the form in
        the console and the questions an agent asks cannot drift apart.
        """
        bt = state.project.backtest
        if bt is None:
            raise HTTPException(422, "this project has no `backtest:` block")
        span: dict[str, Any] = {}
        if state.store.exists(bt.prices):
            col = state.store.time_column(bt.prices, state.project.time_columns.get(bt.prices))
            if col:
                df = state.store.read(bt.prices)
                span = {"earliest": str(df[col].min())[:10], "latest": str(df[col].max())[:10]}
        return {
            # each alpha carries how it wants to be run, so the form can follow the
            # alpha you tick rather than one number for the whole project
            "alphas": [{"id": a, "writes": ref, "name": Project.alpha_name(a, ref),
                        "rebalance": (state.project.job(a) or Step(id="x", script="x")).rebalance,
                        "decay": (state.project.job(a) or Step(id="x", script="x")).decay,
                        "universe": (state.project.job(a) or Step(id="x", script="x")).universe}
                       for a, ref in state.project.alphas],
            "universes": [{"id": u.id, "index": u.index} for u in state.project.universes],
            "data": span,
            "defaults": {"rebalance": bt.rebalance, "decay": bt.decay,
                         "split": bt.split or None, "seed": 0},
            "costs": {"fee_bps": bt.fee_bps, "slippage_bps": bt.slippage_bps,
                      "purge": bt.purge, "embargo": bt.embargo},
        }

    @app.get("/api/backtest/progress")
    def backtest_progress() -> dict[str, Any]:
        """What a replay is doing right now. The console lights the graph off this."""
        from qanat import progress

        return progress.snapshot()

    @app.get("/api/shelf")
    def shelf() -> dict[str, Any]:
        """The ready-made alphas, and what a new one needs to be given."""
        from qanat import alphas as shelf_mod

        wl = state.project.weights_stage
        tables = [t for t in state.project.tables()
                  if not (wl and t.startswith(f"{wl.id}."))]
        return {
            "shelf": shelf_mod.describe(),
            "reads": tables,
            "universes": [{"id": u.id, "index": u.index} for u in state.project.universes],
            "stages": [{"id": st.id, "kind": st.kind} for st in state.project.stages],
        }

    @app.post("/api/alphas")
    def save_alpha(req: AlphaRequest) -> dict[str, Any]:
        """Add an alpha, or change one. One weights table per alpha, so the name is
        the name of that table and of the PnL table under it."""
        from qanat import alphas as shelf_mod
        from qanat.editor import EditorError, save_step

        with state._lock:
            wl = state.project.weights_stage
            if wl is None:
                raise HTTPException(422, "this project has no weights stage")
            name = req.name.strip().lower().replace("-", "_").replace(" ", "_")
            if not name.replace("_", "").isalnum():
                raise HTTPException(422, f"'{req.name}' is not a usable name")
            if req.reads not in state.project.tables():
                raise HTTPException(422, f"no table '{req.reads}' to read from")
            if req.universe and state.project.universe(req.universe) is None:
                raise HTTPException(422, f"no universe '{req.universe}'")

            existing_id = req.id if req.id and state.project.job(req.id) else None
            step_id = existing_id or f"alpha_{name}"
            opts = dict(req.options or {})
            if req.shelf:
                if req.shelf not in shelf_mod.CATALOGUE:
                    raise HTTPException(422, f"no alpha called '{req.shelf}' on the shelf")
                script = shelf_mod.write_alpha(state.root, name, from_shelf=req.shelf)
                base = dict(shelf_mod.CATALOGUE[req.shelf]["options"])
                base.update(opts)
                opts = base
            else:
                existing = state.project.job(step_id)
                if existing is None or not getattr(existing, "script", None):
                    raise HTTPException(422, "a new alpha needs a shelf entry to start from")
                script = existing.script
            opts["reads"] = req.reads
            # An existing step keeps the table it writes. Renaming an alpha would
            # move its weights and PnL tables and orphan every result that names
            # them, so it is not something a settings field should do quietly.
            prior = state.project.job(step_id) if existing_id else None
            writes = list(prior.writes) if prior and prior.writes else [f"{wl.id}.{name}"]
            reads = sorted({*(prior.reads if prior else []), req.reads}) if prior else [req.reads]
            try:
                written = save_step(state.project, state.root, {
                    "id": step_id, "from": reads, "to": writes,
                    "script": script, "universe": req.universe, "options": opts,
                    "rebalance": req.rebalance, "decay": req.decay,
                    "schedule": getattr(prior, "schedule", None),
                }, create_script=False)
            except EditorError as exc:
                raise HTTPException(422, str(exc)) from exc
            state.reload()
        return {"id": step_id, "name": name, "writes": writes[0],
                "script": script, "options": opts, "files": written}

    @app.delete("/api/alphas/{step_id}")
    def drop_alpha(step_id: str) -> dict[str, Any]:
        from qanat.editor import EditorError, delete_step

        with state._lock:
            if state.project.alpha(step_id) is None:
                raise HTTPException(404, f"no alpha '{step_id}'")
            try:
                written = delete_step(state.project, state.root, step_id)
            except EditorError as exc:
                raise HTTPException(422, str(exc)) from exc
            state.reload()
        return {"removed": step_id, "files": written,
                "note": "the script is still on disk, and so is anything it wrote"}

    @app.get("/api/alphas")
    def alpha_book() -> dict[str, Any]:
        """The strategy book: every alpha this project has backtested, and how it did.

        Derived from run history, not from a list someone maintains. An alpha is in
        the book because it produced a result, and the sparkline is that result.
        """
        import json as _json

        from qanat.backtest import pnl_ref

        book = state.store.alpha_book()
        # "wired" no longer means the one alpha: every alpha in the file is live, and
        # each has its own weights table. It now means the newest one to produce a run.
        wired = book[0]["alpha"] if book else None
        from qanat.backtest import _upstream_of, alpha_ids_of

        def _dag_of(project: Project, key: str) -> set[str]:
            """A blend's lineage is every alpha's lineage, so selecting it lights the
            whole of what was priced rather than half of it."""
            ids = alpha_ids_of(key)
            return set().union(*(_upstream_of(project, i) for i in ids)) if ids else set()

        for row in book:
            row["wired"] = row["alpha"] == wired
            # An alpha is its whole DAG, not only the step that ends it: the console
            # draws this set and nothing else when the alpha is selected.
            row["dag"] = sorted(_dag_of(state.project, row["alpha"]))
            run = state.store.backtest(row["last_run_id"])
            report = _json.loads(run["report"]) if run and run.get("report") else {}
            seg = report.get("segments") or {}
            row["conditions"] = report.get("conditions") or {}
            row["out_of_sample"] = (seg.get("out_of_sample") or {}).get("net")
            row["in_sample"] = (seg.get("in_sample") or {}).get("net")
            # a sparkline of the last run: equity, thinned to something drawable
            eq, v = [], 1.0
            for p_ in report.get("periods") or []:
                v *= 1 + p_["net"]
                eq.append(round(v, 6))
            step = max(1, len(eq) // 60)
            row["spark"] = eq[::step][:60]
        # alphas declared in the file but never yet backtested still belong in the book
        for step_id, ref in state.project.alphas:
            if not any(r["alpha"] == step_id for r in book):
                book.append({"alpha": step_id, "runs": 0, "wired": step_id == wired,
                             "spark": [], "last_run_id": None, "last_net": None,
                             "best_net": None, "out_of_sample": None, "in_sample": None,
                             "conditions": {}, "writes": ref,
                             "dag": sorted(_dag_of(state.project, step_id))})
        for row in book:
            # a blend has no step of its own: it is priced from several weights
            # tables, and the row should say which
            ids = alpha_ids_of(row["alpha"])
            refs = [f[1] for f in (state.project.alpha(i) for i in ids) if f]
            row["reads_weights"] = refs
            row["writes"] = refs[0] if len(refs) == 1 else None
            row["name"] = Project.alpha_name(row["alpha"], row["writes"] or "")
            row["pnl"] = pnl_ref(state.project, row["alpha"])
            # what the step itself says, so the editor shows the rule rather than
            # whatever the last run happened to override it with
            st = state.project.job(row["alpha"])
            row["rebalance"] = getattr(st, "rebalance", None)
            row["decay"] = getattr(st, "decay", None)
            row["universe"] = getattr(st, "universe", None)
            row["reads"] = list(getattr(st, "reads", []) or [])
            row.setdefault("dag", [])
        return {"wired": wired, "alphas": book, "stats": _book_stats(state.store, book)}

    @app.get("/api/backtests")
    def backtests(limit: int = 25) -> list[dict[str, Any]]:
        return state.store.backtests(limit)

    @app.get("/api/backtests/{run_id}")
    def backtest_one(run_id: int) -> dict[str, Any]:
        import json as _json

        row = state.store.backtest(run_id)
        if row is None:
            raise HTTPException(404, f"no backtest {run_id}")
        return _json.loads(row["report"]) if row.get("report") else dict(row)

    @app.get("/api/backtests/{run_id}/periods/{as_of}")
    def backtest_period(run_id: int, as_of: str) -> dict[str, Any]:
        """One point on the curve, opened up: what was held, and which name did it."""
        from qanat.backtest import BacktestError, period_detail

        try:
            return period_detail(state.store, state.project, run_id, as_of)
        except BacktestError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/backtests/{run_id}/weights")
    def backtest_weights(run_id: int, as_of: str | None = None) -> dict[str, Any]:
        if state.store.backtest(run_id) is None:
            raise HTTPException(404, f"no backtest {run_id}")
        return {"run_id": run_id, "weights": state.store.bt_weights(run_id, as_of)}

    @app.get("/api/backtests/{a}/compare/{b}")
    def backtest_compare(a: int, b: int) -> dict[str, Any]:
        from qanat.backtest import compare as compare_runs

        ra, rb = state.store.backtest(a), state.store.backtest(b)
        for rid, row in ((a, ra), (b, rb)):
            if row is None:
                raise HTTPException(404, f"no backtest {rid}")
        return compare_runs(ra, rb)

    @app.post("/api/backtest")
    def backtest_run(req: BacktestRequest) -> dict[str, Any]:
        from qanat.backtest import BacktestError, run_backtest

        if not state._replay.acquire(blocking=False):
            raise HTTPException(409, "a replay is already running in this project")
        try:
            # Read the project once, then let go: the console keeps polling the whole
            # time this runs, and it is reading, not editing.
            with state._lock:
                project, root = state.project, state.root
            res = run_backtest(
                state.store, project, root, req.frm, req.to,
                rebalance=req.rebalance, seed=req.seed,
                decay=req.decay, universe=req.universe, split=req.split,
                alpha=req.alpha, allocation=req.allocation,
                fee_bps=req.fee_bps, slippage_bps=req.slippage_bps,
                purge=req.purge, embargo=req.embargo,
            )
        except BacktestError as exc:
            raise HTTPException(422, str(exc)) from exc
        finally:
            state._replay.release()
        return res.as_dict()

    @app.get("/api/events")
    def events(limit: int = 200) -> list[dict[str, Any]]:
        return state.store.recent_events(limit)

    @app.get("/api/runs")
    def runs(limit: int = 60) -> list[dict[str, Any]]:
        return state.store.recent_runs(limit)

    @app.get("/api/table/{stage}/{name}")
    def table(
        stage: str, name: str, limit: int = 25, offset: int = 0,
        order: str | None = None, desc: bool = True,
    ) -> dict[str, Any]:
        """A page of a table. The console reads the data itself here, not a sample."""
        ref = f"{stage}.{name}"
        info = state.store.table_info(ref)
        if info is None:
            raise HTTPException(404, f"{ref} has not been written yet")
        cols = [c for c, _ in info.columns]
        if order and order not in cols:
            raise HTTPException(422, f"no column '{order}' in {ref}")
        order_col = order or next(
            (c for c in cols if c.lower() in ("as_of", "ts", "date", "time")), None
        )
        sql = f'SELECT * FROM "{stage}__{name}"'
        if order_col:
            sql += f' ORDER BY "{order_col}" ' + ("DESC" if desc else "ASC")
        limit = max(1, min(500, int(limit)))
        offset = max(0, int(offset))
        df = state.store.query(f"{sql} LIMIT {limit} OFFSET {offset}")
        return {
            "ref": ref,
            "rows": info.rows,
            "limit": limit,
            "offset": offset,
            "order": order_col,
            "desc": desc,
            "updated_at": info.updated_at,
            "retention": state.project.retention.get(ref),
            "columns": [{"name": c, "type": t} for c, t in info.columns],
            "sample": [
                {k: _plain(v) for k, v in rec.items()} for rec in df.to_dict("records")
            ],
        }

    @app.get("/api/jobs/{job_id}")
    def job_detail(job_id: str) -> dict[str, Any]:
        """The operator behind a step: its script, its options, and what it last did.

        The graph draws jobs now, so clicking one has to answer "what does this
        actually compute" without sending anyone to a text editor.
        """
        with state._lock:
            job = state.project.job(job_id)
            if job is None:
                raise HTTPException(404, f"no job called '{job_id}'")
            root, project = state.root, state.project
        kind = "source" if hasattr(job, "connector") else "step"
        out: dict[str, Any] = {
            "id": job.id,
            "kind": kind,
            "reads": list(job.reads),
            "writes": list(job.writes),
            "schedule": job.schedule,
            "when": list(getattr(job, "when", [])),
            "options": dict(job.options),
            "universe": getattr(job, "universe", None),
            "connector": getattr(job, "connector", None),
            "mode": getattr(job, "mode", None),
            "script": getattr(job, "script", None),
            "last_run": state.store.last_run_by_job().get(job.id),
        }
        if out["script"]:
            path = root / out["script"]
            out["language"] = path.suffix.lstrip(".")
            out["source"] = path.read_text() if path.is_file() else None
            if out["source"] is None:
                out["error"] = f"{out['script']} is declared but not on disk"
        if out["universe"]:
            u = project.universe(out["universe"])
            out["universe_file"] = u.symbols if u else None
        if kind == "source":
            out["connection"] = _connection_of(job, root)
        return out

    @app.post("/api/jobs/{job_id}/run")
    def run_now(job_id: str) -> JSONResponse:
        with state._lock:
            if state.project.job(job_id) is None:
                raise HTTPException(404, f"no job called '{job_id}'")
            if state.sched is None:
                raise HTTPException(409, "this server was started without a scheduler")
            state.sched.fire(job_id)
        return JSONResponse({"queued": job_id})

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "project": state.project.name, "version": __version__}

    if CONSOLE.is_dir():
        app.mount("/static", StaticFiles(directory=CONSOLE), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(CONSOLE / "index.html")

    return app


def _plain(v: Any) -> Any:
    if isinstance(v, float) and math.isnan(v):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ", timespec="seconds")
    if hasattr(v, "item"):
        try:
            return v.item()
        except Exception:  # noqa: BLE001
            return str(v)
    return v
