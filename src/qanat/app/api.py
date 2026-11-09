"""The routes. Each one: read the request, call one service function, answer.

No SQL here and no `store.` call beyond handing the store to a service
function -- `test_the_app_layer_holds_no_sql` reads this file to hold that.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import Response

STATIC = Path(__file__).parent / "static"

#: The pages change with the code, and a browser holding last week's `app.js`
#: runs last week's page against this week's API. `no-cache` still lets it keep
#: a copy -- it only has to ask whether the copy is current, which the ETag answers.
NO_CACHE = {"Cache-Control": "no-cache"}

#: What each route does, as the tool that does the same thing. The route is
#: allowed when that tool is offered at the server's scope. Routes with no tool
#: behind them read, and read at `data`.
SAME_AS: dict[str, str] = {
    "find": "find_filings",
    "ingest": "ingest_filings",
    "run": "run_project",
    "executions": "list_executions",
    "metrics": "list_metrics",
    "define_metric": "define_metric",
    "remove_metric": "remove_metric",
    "filters": "list_filters",
    "save_filter": "save_filter",
    "remove_filter": "remove_filter",
    "screen": "screen_companies",
    "coverage": "check_coverage",
    "definitions": "list_definitions",
    "schedules": "list_subscriptions",
    # A removal has no tool yet. It is pinned to the scope of the write it most
    # resembles -- `ingest_filings` put the filing here, and taking it back out
    # is not a wider act than putting it in.
    "removal": "ingest_filings",
    "projects": "list_projects",
    "select_project": "select_project",
    "close_project": "close_project",
    "create_project": "create_project",
    "delete_project": "delete_project",
    # The contact is what an ingest sends, so setting it is the ingest's scope.
    "user_agent": "ingest_filings",
}


class NoProject(LookupError):
    """Nothing selected and nothing to select: the page goes to Settings."""


class _Selected:
    """The project the app is working in, read at the moment of each call.

    Every route reads `s.store`, `s.project` and `s.root`, as it did when a
    server held one project. Now `s` resolves to whichever project the person
    selected, so a switch on the settings page moves every page with it.
    """

    def __init__(self, current: Callable[[], Any]) -> None:
        self._current = current

    def __getattr__(self, name: str) -> Any:
        o = self._current()
        if o is None:
            raise NoProject("There is no project to open.")
        return getattr(o.session, name)


def _load_pages() -> tuple[str, dict[str, tuple[bytes, str]]]:
    """The page shell and its assets, read once, when the server starts.

    **Read per request, a running server served whatever was on disk** -- so a
    server started before a change handed out the new page against its own old
    API, and the page showed "never" for fields that API did not send. Read at
    start, a server always serves the page its own code was written for.

    The shell carries `?v=<hash>` on each asset, so a changed file has a new
    address and no browser cache can hold the old one under it.
    """
    import hashlib
    import mimetypes

    assets: dict[str, tuple[bytes, str]] = {}
    for path in STATIC.iterdir():
        if path.is_file():
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            assets[path.name] = (path.read_bytes(), kind)
    html = assets.pop("index.html")[0].decode()
    for name in ("app.css", "app.js", "favicon.svg"):
        digest = hashlib.sha256(assets[name][0]).hexdigest()[:12]
        html = html.replace(f"/static/{name}\"", f"/static/{name}?v={digest}\"")
    return html, assets


def _clean(x: Any) -> Any:
    """JSON a browser can parse: NaN and infinity become null, the rest as MCP
    sends it (anything unknown as its string)."""
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else x
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    return x


def _json(obj: Any, status: int = 200) -> Response:
    body = json.loads(json.dumps(obj, default=str))
    return Response(content=json.dumps(_clean(body)), status_code=status,
                    media_type="application/json")


def mount(app: FastAPI, workspace: Any, scope: str) -> None:
    """Add `/api/...` and the pages to an app that already serves `/mcp`.

    `workspace` holds the projects. The app is one holder in it, so the pages
    work in the project selected here -- or, before anything is selected, the one
    used last. With no project at all, a project route answers 409 and the page
    goes to Settings.
    """
    from qanat import __version__
    from qanat.catalog import CatalogError
    from qanat.filings import FilingsError
    from qanat.filters import FilterError
    from qanat.mcp import TOOLS, rank
    from qanat.metrics import MetricError
    from qanat.projects import APP, THEMES, ProjectsError
    from qanat.reading import ReadingError
    from qanat.removal import RemovalError
    from qanat.screen import ScreenError
    from qanat.store import StoreBusy, acting_as

    refusals = (CatalogError, FilingsError, FilterError, MetricError, ProjectsError,
                ReadingError, RemovalError, ScreenError, StoreBusy, ValueError, KeyError,
                LookupError)

    #: The app opens on the project used last once, when it first loads. After
    #: the person closes a project it stays closed until they open another --
    #: jumping to some other project on its own would be a switch nobody asked for.
    opened_once = {"done": False}

    def current() -> Any:
        """The project the app works in -- the one used last, the first time."""
        name = workspace.selected(APP)
        if name is None:
            if opened_once["done"]:
                return None
            opened_once["done"] = True
            name = workspace.latest()
            if name is None:
                return None
            workspace.select(APP, name)
        opened_once["done"] = True
        return workspace.open(name)
    tool_scope = {t["name"]: t["scope"] for t in TOOLS}

    def allowed(what: str) -> bool:
        need = tool_scope.get(SAME_AS.get(what, ""), "data")
        return rank(scope) >= rank(need)

    def can() -> dict[str, bool]:
        return {k: allowed(k) for k in SAME_AS}

    async def call(what: str, fn: Callable[[], Any], writes: bool = False) -> Response:
        if not allowed(what):
            need = tool_scope[SAME_AS[what]]
            return _json({"detail": f"this server was started at scope '{scope}', and "
                                    f"this needs '{need}'. Start it with --scope {need}."},
                         403)

        def run() -> Any:
            o = current()
            if o is None:
                raise NoProject("There is no project to open.")
            # One store, one writer -- the same lock `/mcp` takes for this
            # project. And the person at the page is the cause of what runs.
            with o.lock, acting_as("you"):
                out = fn()
                if writes:
                    o.session.reload()
                    workspace.touch(APP)
                return out

        try:
            out = await anyio.to_thread.run_sync(run)
        except NoProject as exc:
            return _json({"detail": str(exc), "no_project": True}, 409)
        except refusals as exc:
            return _json({"detail": str(exc).strip("'\"")}, 400)
        except Exception as exc:  # noqa: BLE001 -- a page reads JSON, never a bare 500
            return _json({"detail": f"{type(exc).__name__}: {exc}"}, 500)
        return _json(out)

    async def body(request: Request) -> dict[str, Any]:
        try:
            got = await request.json()
        except Exception:  # noqa: BLE001 -- any unreadable body is the same answer
            return {}
        return got if isinstance(got, dict) else {}

    def q(request: Request, key: str, default: Any = None) -> Any:
        v = request.query_params.get(key)
        return default if v in (None, "") else v

    async def home_call(what: str, fn: Callable[[], Any]) -> Response:
        """A call on the home folder: no project needed, no project's lock taken."""
        if not allowed(what):
            need = tool_scope[SAME_AS[what]]
            return _json({"detail": f"this server was started at scope '{scope}', and "
                                    f"this needs '{need}'. Start it with --scope {need}."},
                         403)
        try:
            out = await anyio.to_thread.run_sync(fn)
        except refusals as exc:
            return _json({"detail": str(exc).strip("'\"")}, 400)
        except Exception as exc:  # noqa: BLE001 -- a page reads JSON, never a bare 500
            return _json({"detail": f"{type(exc).__name__}: {exc}"}, 500)
        return _json(out)

    s = _Selected(current)

    # ---- the shell -------------------------------------------------------------
    @app.get("/api/health")
    async def health():  # type: ignore[no-untyped-def]
        def read() -> dict[str, Any]:
            try:
                o = current()
            except refusals:
                o = None
            # A home names a project by its folder; a single served project by
            # the name in its own file, as it always has.
            name = (o.session.project.name if workspace.pinned else o.name) if o else None
            return {"project": name, "version": __version__,
                    "scope": scope, "can": can(), "theme": workspace.theme(),
                    "home": str(workspace.home) if workspace.home else None,
                    "manages_projects": workspace.pinned is None}
        return _json(await anyio.to_thread.run_sync(read))

    # ---- projects and settings ------------------------------------------------
    @app.get("/api/projects")
    async def projects():  # type: ignore[no-untyped-def]
        def read() -> dict[str, Any]:
            current()       # the one used last is the one the app works in
            return workspace.list(APP)
        return await home_call("projects", read)

    @app.post("/api/projects")
    async def create_project(request: Request):  # type: ignore[no-untyped-def]
        name = str((await body(request)).get("name") or "").strip()
        return await home_call("create_project", lambda: workspace.create(name))

    @app.post("/api/projects/{name}/select")
    async def select_project(name: str):  # type: ignore[no-untyped-def]
        """Open a project in the app. **One at a time: close the current one first.**"""
        def pick() -> Any:
            opened_once["done"] = True
            return workspace.select(APP, name)      # refuses while another is open
        return await home_call("select_project", pick)

    @app.post("/api/projects/close")
    async def close_project():  # type: ignore[no-untyped-def]
        """Stop working in the current project, and release it if nobody else is in it."""
        def shut() -> Any:
            if workspace.pinned is not None:
                raise ProjectsError("this server serves one project, and it cannot be closed.")
            opened_once["done"] = True
            return workspace.close(APP)
        return await home_call("close_project", shut)

    @app.get("/api/projects/{name}/deletion")
    async def deletion_preview(name: str):  # type: ignore[no-untyped-def]
        """What deleting would take. Deletes nothing."""
        return await home_call("delete_project",
                               lambda: workspace.delete(name, APP, confirm=False))

    @app.delete("/api/projects/{name}")
    async def delete_project(name: str, request: Request):  # type: ignore[no-untyped-def]
        # The name typed back, checked here and not only on the page.
        if q(request, "confirm") != name:
            return _json({"detail": f"type the project's name to delete it: "
                                    f"?confirm={name}"}, 400)
        return await home_call("delete_project",
                               lambda: workspace.delete(name, APP, confirm=True))

    @app.get("/api/settings")
    async def settings_():  # type: ignore[no-untyped-def]
        def read() -> dict[str, Any]:
            o = current()
            name = o.name if o else None
            return {"theme": workspace.theme(), "themes": list(THEMES),
                    "home": str(workspace.home) if workspace.home else None,
                    "project": name,
                    "user_agent": workspace.user_agent(name) if name else None}
        return await home_call("projects", read)

    @app.put("/api/settings/theme")
    async def set_theme(request: Request):  # type: ignore[no-untyped-def]
        theme = str((await body(request)).get("theme") or "")
        return await home_call("projects", lambda: {"theme": workspace.set_theme(theme)})

    @app.put("/api/projects/{name}/settings")
    async def set_project_settings(name: str, request: Request):  # type: ignore[no-untyped-def]
        ua = str((await body(request)).get("user_agent") or "")
        return await home_call("user_agent", lambda: workspace.set_user_agent(name, ua))

    # ---- getting data in --------------------------------------------------------
    @app.get("/api/filings/find")
    async def find(request: Request):  # type: ignore[no-untyped-def]
        from qanat.filings import find as svc

        companies = [c.strip() for c in str(q(request, "companies", "")).split(",")
                     if c.strip()]
        if not companies:
            return _json({"detail": "name at least one company: a ticker or a CIK"}, 400)
        return await call("find", lambda: svc(
            s.store, s.project, companies, form=q(request, "form"),
            frm=q(request, "from"), to=q(request, "to"),
            limit=int(q(request, "limit", 50)), root=s.root))

    @app.post("/api/filings/ingest")
    async def ingest(request: Request):  # type: ignore[no-untyped-def]
        from qanat.filings import ingest as svc

        chosen = (await body(request)).get("filings") or []
        if not chosen:
            return _json({"detail": "pick at least one filing: [{company, accession}]"},
                         400)
        return await call("ingest", lambda: svc(
            s.store, s.project, s.root, [dict(c) for c in chosen]), writes=True)

    @app.post("/api/run")
    async def run_(request: Request):  # type: ignore[no-untyped-def]
        from qanat.runner import run

        b = await body(request)
        # The page transforms what has landed; it does not re-poll every feed.
        return await call("run", lambda: run(
            s.store, s.project, s.root, definition=b.get("definition") or None,
            ingest=bool(b.get("ingest"))), writes=True)

    # ---- what is held -----------------------------------------------------------
    @app.get("/api/companies")
    async def companies(request: Request):  # type: ignore[no-untyped-def]
        from qanat.reading import list_companies

        return await call("companies", lambda: list_companies(
            s.store, s.project, query=q(request, "query"),
            limit=int(q(request, "limit", 100))))

    @app.get("/api/companies/{filer}/filings")
    async def company_filings(filer: str, request: Request):  # type: ignore[no-untyped-def]
        from qanat.catalog import progress

        return await call("progress", lambda: progress(
            s.store, s.project, filer, limit=int(q(request, "limit", 20))))

    @app.get("/api/executions")
    async def executions(request: Request):  # type: ignore[no-untyped-def]
        from qanat.catalog import executions as svc

        return await call("executions", lambda: svc(s.store, int(q(request, "limit", 30))))

    @app.get("/api/definitions")
    async def definitions(request: Request):  # type: ignore[no-untyped-def]
        from qanat.catalog import definitions as svc

        return await call("definitions", lambda: svc(
            s.store, s.project, str(q(request, "stage", ""))))

    @app.get("/api/schedules")
    async def schedules(request: Request):  # type: ignore[no-untyped-def]
        """The cadences, and whether anything in *this* process reads them.

        **A clock is a property of the running program, not of the project.** A
        schedule under `qanat serve --app` with no `--schedule` is declared and
        not running, and saying so is the honest answer rather than "updating
        daily" -- the same sentence `list_subscriptions` gives.
        """
        def read() -> dict[str, Any]:
            attached = getattr(getattr(s, "state", None), "sched", None) is not None
            rows = [{"id": sc.id, "definition": sc.definition, "cron": sc.cron,
                     "enabled": getattr(sc, "enabled", True),
                     "definition_exists": s.project.definition(sc.definition) is not None}
                    for sc in s.project.job_schedules]
            return {"schedules": rows, "clock_attached": attached,
                    "note": ("a clock is attached in this process"
                             if attached else
                             "nothing in this process reads a cron line; these run when a "
                             "recorder runs (`qanat schedule`, or `--schedule` on this server)")}
        return await call("schedules", read)

    # ---- taking a filing back out --------------------------------------------------
    @app.get("/api/filings/{accession}/removal")
    async def removal_preview(accession: str):  # type: ignore[no-untyped-def]
        """What a removal would take. Removes nothing."""
        from qanat.removal import preview

        return await call("removal", lambda: preview(s.store, s.project, accession))

    @app.delete("/api/filings/{accession}")
    async def remove_filing(accession: str):  # type: ignore[no-untyped-def]
        from qanat.removal import remove

        return await call("removal", lambda: remove(s.store, s.project, s.root, accession),
                          writes=True)

    # ---- metrics ----------------------------------------------------------------
    @app.get("/api/metrics")
    async def metrics():  # type: ignore[no-untyped-def]
        from qanat.metrics import listing

        return await call("metrics", lambda: {"metrics": listing(s.project, s.store)})

    @app.post("/api/metrics")
    async def define_metric(request: Request):  # type: ignore[no-untyped-def]
        from qanat.metrics import define

        b = await body(request)
        return await call("define_metric", lambda: define(
            s.store, s.project, s.root, mid=str(b.get("id") or ""),
            sql=str(b.get("sql") or ""), unit=str(b.get("unit") or ""),
            description=str(b.get("description") or "")), writes=True)

    @app.delete("/api/metrics/{mid}")
    async def remove_metric(mid: str, request: Request):  # type: ignore[no-untyped-def]
        from qanat.metrics import remove

        return await call("remove_metric", lambda: remove(
            s.project, s.root, mid=mid, force=q(request, "force") == "true"),
            writes=True)

    @app.get("/api/metrics/{mid}/formula")
    async def metric_formula(mid: str):  # type: ignore[no-untyped-def]
        from qanat.metrics import formula

        return await call("metrics", lambda: {"id": mid,
                                              "sql": formula(s.project, s.root, mid)})

    @app.get("/api/metrics/{mid}/trace")
    async def trace_metric(mid: str, request: Request):  # type: ignore[no-untyped-def]
        from qanat.catalog import trace_value

        company = q(request, "company")
        as_of = q(request, "as_of")
        if not company or not as_of:
            return _json({"detail": "a trace needs a company and an as_of date"}, 400)
        return await call("metrics", lambda: trace_value(
            s.store, s.project, mid, company, as_of))

    # ---- filters and the screen -------------------------------------------------
    @app.get("/api/filters")
    async def filters_():  # type: ignore[no-untyped-def]
        from qanat.filters import index

        return await call("filters", lambda: index(s.project))

    @app.put("/api/filters/{fid}")
    async def save_filter(fid: str, request: Request):  # type: ignore[no-untyped-def]
        from qanat.filters import save

        b = await body(request)
        return await call("save_filter", lambda: save(
            s.project, s.root, fid, b.get("clauses"),
            description=str(b.get("description") or "")), writes=True)

    @app.delete("/api/filters/{fid}")
    async def remove_filter(fid: str):  # type: ignore[no-untyped-def]
        from qanat.filters import remove

        return await call("remove_filter", lambda: remove(s.project, s.root, fid),
                          writes=True)

    @app.post("/api/screen")
    async def screen(request: Request):  # type: ignore[no-untyped-def]
        from qanat.screen import run

        b = await body(request)
        companies = b.get("companies") or None
        return await call("screen", lambda: run(
            s.store, s.project, filter=b.get("filter") or None,
            clauses=b.get("clauses"), as_of=b.get("as_of"),
            frm=b.get("from"), to=b.get("to"), every=b.get("every") or "1y",
            companies=companies, root=s.root))

    @app.get("/api/coverage")
    async def coverage():  # type: ignore[no-untyped-def]
        from qanat.screen import coverage as svc

        return await call("coverage", lambda: svc(s.store, s.project, None))

    # ---- the table reader -------------------------------------------------------
    @app.get("/api/tables")
    async def tables():  # type: ignore[no-untyped-def]
        from qanat.catalog import tables as svc

        return await call("tables", lambda: svc(s.store, s.project))

    @app.get("/api/tables/{ref}")
    async def table(ref: str, request: Request):  # type: ignore[no-untyped-def]
        from qanat.catalog import describe, sample

        def both() -> dict[str, Any]:
            return {"table": describe(s.store, s.project, ref),
                    "sample": sample(s.store, s.project, ref,
                                     limit=int(q(request, "limit", 50)),
                                     as_of=q(request, "as_of"))}

        return await call("tables", both)

    @app.get("/api/tables/{ref}/profile")
    async def table_profile(ref: str):  # type: ignore[no-untyped-def]
        from qanat.catalog import profile

        return await call("tables", lambda: profile(s.store, s.project, ref))

    @app.get("/api/tables/{ref}/trace")
    async def table_trace(ref: str):  # type: ignore[no-untyped-def]
        from qanat.catalog import trace

        return await call("tables", lambda: trace(s.project, ref))

    # ---- the pages --------------------------------------------------------------
    @app.get("/api/{rest:path}")
    async def no_route(rest: str):  # type: ignore[no-untyped-def]
        return _json({"detail": f"no route /api/{rest}"}, 404)

    shell, assets = _load_pages()

    @app.get("/static/{name}")
    async def static(name: str):  # type: ignore[no-untyped-def]
        if name not in assets:
            return _json({"detail": "not found"}, 404)
        body, kind = assets[name]
        return Response(content=body, media_type=kind, headers=NO_CACHE)

    @app.get("/")
    async def index():  # type: ignore[no-untyped-def]
        """The page shell, with each asset's address stamped by its content.

        A browser that cached `app.css` before `no-cache` was sent kept using it,
        and drew the new page with the old styles. A changed file now has a new
        address, so no cache can hold the old one under it.
        """
        return Response(content=shell, media_type="text/html", headers=NO_CACHE)


__all__ = ["SAME_AS", "mount"]
