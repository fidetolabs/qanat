"""The same tools, reached over HTTP instead of a pipe.

`qanat mcp` speaks JSON-RPC on stdin and stdout, which is the right transport for
one person on one machine and cannot be the hosted one: a request arriving at a
server has nowhere to keep a child process, and the client is somewhere else
entirely. This module is the other end -- MCP's Streamable HTTP transport over
the same `_dispatch`, the same tools, and the same scopes.

Three things are deliberately fixed here rather than left to the caller.

**The scope is pinned at startup.** A caller that names its own scope has no
scope at all, and each scope is a published contract -- adding a tool to `data`
changes what somebody has already built against. The flag that starts the
server decides; nothing in a request can widen it.

**One process may hold several projects, and a session works in one.** Started
with a home folder, the program opens a project when somebody selects it, and
each MCP session carries the project it selected -- every tool lands there, and
`select_project` moves it. Each project keeps its own store and its own lock,
so two projects never wait on each other and one project never has two writers.
Serving many tenants is still a process and a store apiece.

**A non-loopback bind needs a token.** Binding to an address the world can reach
and then answering anybody is not a default worth having, so it is refused.
"""

from __future__ import annotations

import os
import secrets
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from qanat import __version__
from qanat.mcp import PROTOCOL, TOOLS, ScopeError, _dispatch, rank, tools_for

# FastAPI resolves a route's annotations against this module's globals, and
# `from __future__ import annotations` turns them into strings -- so `Request`
# has to be importable from here or every route reads it as a query parameter.
# Importing app things at module scope is safe because nothing imports this
# module unless `--http` was asked for.

#: The names this server may be reached by.
#:
#: At `full` this server writes transform scripts and runs them, so anything that can
#: reach it can run code on the machine serving it. Binding to loopback is not
#: enough on its own: a site the person visits can point its own domain at
#: 127.0.0.1, and the browser then calls this server believing it is same-origin.
#: CORS never enters into it. The name the browser asked for does, in `Host`.
#:
#: Serving under a real hostname is a deliberate act, so it is a deliberate setting:
#: `QANAT_ALLOWED_HOSTS=qanat.example.com`, comma separated.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _hostname(value: str) -> str:
    """The host in a `Host` header or an `Origin`, without scheme or port."""
    v = value.strip()
    if "//" in v:                       # http://host:port -> host:port
        v = v.split("//", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):               # [::1]:8421 -> ::1
        end = v.find("]")
        return v[1:end] if end > 0 else v
    head, sep, tail = v.rpartition(":")
    return head if sep and tail.isdigit() else v


def allowed_hosts(extra: list[str] | None = None) -> set[str]:
    """Loopback, plus whatever `QANAT_ALLOWED_HOSTS` and the caller add."""
    import os

    names = {h.lower() for h in LOOPBACK_HOSTS}
    for group in (os.environ.get("QANAT_ALLOWED_HOSTS", "").split(","), extra or []):
        names |= {h.strip().lower() for h in group if h and h.strip()}
    return names


#: How long a session may sit idle before it is forgotten. The client is told to
#: start again with `initialize`, which costs it one round trip and costs us
#: nothing to keep.
IDLE_SECONDS = 60 * 60

#: A ceiling so a client that never sends `DELETE` cannot grow the table without
#: end. The oldest goes first, and it is the one least likely to come back.
MAX_SESSIONS = 512


class Sessions:
    """Who has said hello. Identity only -- the project and the store are shared."""

    def __init__(self, on_gone: Any = None) -> None:
        self._seen: dict[str, float] = {}
        self._on_gone = on_gone or (lambda sid: None)

    def open(self) -> str:
        self.sweep()
        if len(self._seen) >= MAX_SESSIONS:
            oldest = min(self._seen, key=lambda k: self._seen[k])
            self._seen.pop(oldest, None)
            self._on_gone(oldest)
        sid = secrets.token_urlsafe(24)
        self._seen[sid] = time.monotonic()
        return sid

    def touch(self, sid: str) -> bool:
        self.sweep()
        if sid not in self._seen:
            return False
        self._seen[sid] = time.monotonic()
        return True

    def close(self, sid: str) -> bool:
        gone = self._seen.pop(sid, None) is not None
        if gone:
            self._on_gone(sid)
        return gone

    def sweep(self) -> None:
        cutoff = time.monotonic() - IDLE_SECONDS
        for sid in [s for s, t in self._seen.items() if t < cutoff]:
            del self._seen[sid]
            self._on_gone(sid)

    def __len__(self) -> int:
        return len(self._seen)


def _error(code: int, message: str, mid: Any = None) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def create_app(
    project_path: str | None,
    scope: str,
    token: str | None = None,
    allow_hosts: list[str] | None = None,
    state: Any = None,
    doors: Sequence[str] = ("mcp",),
    serving: str | None = None,
    workspace: Any = None,
    start_in: str | None = None,
):
    """A FastAPI app carrying the doors it is asked for.

    **`doors` names what gets mounted, and nothing is mounted by default but
    `/mcp`.** `mcp` puts the tool endpoint on; `app` puts the pages and `/api`
    on. Both is one program with two doors, which is the ordinary case for one
    engineer. `/health` is the server answering for itself and is not a door.

    **`workspace` is a home folder of projects**, several of them open at once,
    each MCP session working in the one it selected. Without it this serves the
    one project at `project_path` -- or the one `state` hands over, already
    open with its scheduler -- the way every server did before there was a home.

    `start_in` is the project the program was started inside: the app opens on
    it, and an MCP session starts with it selected.
    """
    from qanat.project import load
    from qanat.projects import APP, Workspace
    from qanat.store import set_actor

    serves_mcp, serves_app = "mcp" in doors, "app" in doors
    if not (serves_mcp or serves_app):
        # A programming error rather than a user's: the CLI refuses an empty
        # `--mcp --app` pair with a message, and nothing else builds one.
        raise ValueError("create_app needs at least one door: 'mcp' or 'app'")

    rank(scope)  # fail before binding a port rather than on the first call
    offered = tools_for(scope)

    # Everything this process does is an agent doing it, exactly as on stdio.
    set_actor("agent")
    if workspace is None:
        root = Path(state.root) if state is not None else load(project_path)[1]
        workspace = Workspace(pinned=root, serving=serving, doors=tuple(doors), state=state)
        # Open AND take the writer lock now, so a store held by another program
        # is reported here rather than on the first write a door takes. Opening
        # alone does not take it: `meta.duckdb` is attached lazily, by design, so
        # that a reader never has to.
        try:
            workspace.open(root.name)
        except Exception:
            workspace.close_all()
            raise
        start_in = root.name
    pinned = workspace.pinned is not None
    if start_in and not pinned:
        workspace.select(APP, start_in)

    # A session that goes -- closed, or forgotten after an hour idle -- stops
    # working in its project, or it would block that project's delete forever.
    sessions = Sessions(on_gone=workspace.release)
    allowed = allowed_hosts(allow_hosts)

    title = workspace.pinned.name if pinned else f"home {workspace.home}"
    app = FastAPI(
        title=f"qanat · {title}",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    #: The `/mcp` routes, held off the app until `doors` asks for them. Collected
    #: on a router rather than registered straight on the app because a decorator
    #: runs when it is read, and there is no removing a route afterwards.
    mcp_door = APIRouter()

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Refuse a name we do not answer to, and a caller without the token.

        Binding to loopback does not stop a page the person is visiting from
        pointing its own domain at 127.0.0.1 and calling this server believing it
        is same-origin. CORS never enters into it. The name the browser asked for
        does, and it arrives in `Host`.
        """
        host = _hostname(request.headers.get("host", "")).lower()
        if host not in allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": f"this server answers to {', '.join(sorted(allowed))}, "
                                   f"not {host or '(no host header)'}. Set "
                                   f"QANAT_ALLOWED_HOSTS to serve it under another name."},
            )
        origin = request.headers.get("origin")
        if origin and _hostname(origin).lower() not in allowed:
            return JSONResponse(
                status_code=403,
                content={"detail": f"request from {origin} was refused."},
            )
        # The page shell holds no data, and a browser cannot attach a bearer
        # header to a page load -- so it alone passes without the token, and every
        # call it then makes to /api sends the token the person typed.
        shell = serves_app and (request.url.path == "/" or request.url.path.startswith("/static/"))
        if token and not shell:
            sent = request.headers.get("authorization", "")
            prefix, _, value = sent.partition(" ")
            # Constant time, because a token that can be guessed one character at
            # a time is not a token.
            if prefix.lower() != "bearer" or not secrets.compare_digest(value, token):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "this server needs a bearer token."},
                    headers={"WWW-Authenticate": 'Bearer realm="qanat"'},
                )
        return await call_next(request)

    @mcp_door.post("/mcp")
    async def post_mcp(request: Request):  # type: ignore[no-untyped-def]
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 -- any unreadable body is the same answer
            return JSONResponse(status_code=400, content=_error(-32700, "parse error"))

        messages = body if isinstance(body, list) else [body]
        if not messages:
            return JSONResponse(status_code=400, content=_error(-32600, "empty request"))

        # `initialize` is how a session starts, so it is the one message that may
        # arrive without a session id.
        starting = any(m.get("method") == "initialize" for m in messages if isinstance(m, dict))
        sid = request.headers.get("mcp-session-id")

        if not starting:
            if not sid:
                return JSONResponse(
                    status_code=400,
                    content=_error(-32600, "send Mcp-Session-Id, or initialize first"),
                )
            if not sessions.touch(sid):
                # 404 is the spec's way of saying "start again"; the client is
                # expected to re-initialize rather than to treat it as fatal.
                return JSONResponse(
                    status_code=404,
                    content=_error(-32600, "this session is gone. Call initialize again."),
                )
        else:
            sid = sessions.open()
            if start_in and not pinned:
                # Started inside a project: a session begins there, as over stdio.
                workspace.select(sid, start_in)

        replies: list[dict] = []
        for one in messages:
            if not isinstance(one, dict):
                replies.append(_error(-32600, "not a JSON-RPC message"))
                continue
            # The tool list is the scope's, not the whole shelf. `_dispatch` reads
            # it for both `tools/list` and `tools/call`, so pinning it here pins
            # both.
            reply = await anyio.to_thread.run_sync(_call, workspace, sid, one, offered)
            if reply is not None:
                replies.append(reply)

        if not replies:
            # Notifications only. Nothing to answer with, and saying so is not an
            # error -- `notifications/initialized` arrives on every handshake.
            return Response(status_code=202)

        headers = {}
        if starting:
            headers["Mcp-Session-Id"] = sid
        payload = replies if isinstance(body, list) else replies[0]
        return JSONResponse(content=payload, headers=headers)

    @mcp_door.get("/mcp")
    async def get_mcp():  # type: ignore[no-untyped-def]
        """No server-initiated messages, so no stream to open.

        The spec allows a server to answer 405 here, and answering it honestly is
        better than holding a connection open that will never carry anything.
        """
        return JSONResponse(
            status_code=405,
            content={"detail": "this server sends nothing on its own; POST to /mcp."},
        )

    @mcp_door.delete("/mcp")
    async def delete_mcp(request: Request):  # type: ignore[no-untyped-def]
        sid = request.headers.get("mcp-session-id")
        if not sid or not sessions.close(sid):
            return JSONResponse(status_code=404, content={"detail": "no such session"})
        return Response(status_code=204)

    @app.get("/health")
    async def health():  # type: ignore[no-untyped-def]
        """Enough to tell a load balancer the store is still open."""
        return {
            "project": workspace.selected(APP),
            "home": str(workspace.home) if workspace.home else None,
            "scope": scope,
            "tools": len(offered),
            "of": len(TOOLS),
            "sessions": len(sessions),
        }

    if serves_mcp:
        # The tool endpoint. A router rather than routes straight on the app, so
        # that `qanat serve --app` serves the pages with *nothing* at `/mcp` --
        # a person who wanted a browser did not ask for a tool endpoint on their
        # port.
        app.include_router(mcp_door)

    if serves_app:
        # The pages and their API, over the same projects and the same locks. A
        # second door onto the service layer, not onto `/mcp`.
        from qanat.app import mount

        mount(app, workspace, scope)

    pinned_open = workspace.open(workspace.pinned.name) if pinned else None
    app.state.qanat_session = pinned_open.session if pinned_open else None
    app.state.qanat_workspace = workspace
    app.state.scope = scope
    app.state.qanat_state = state
    app.state.doors = tuple(doors)
    return app


def _call(workspace: Any, sid: str | None, message: dict, offered: list[dict]) -> dict | None:
    """Run one message against the session's project, one at a time per project.

    Tool calls are blocking and run on worker threads, so without the project's
    lock two requests would be inside one store at once. Two projects are two
    stores and do not wait on each other.
    """
    from qanat.mcp import Home

    home = Home(workspace, sid or "")
    try:
        opened = workspace.opened_for(sid or "")
    except Exception as exc:  # noqa: BLE001 -- a project gone or held: say so as a tool error
        if message.get("method") != "tools/call":
            opened = None
        else:
            return {"jsonrpc": "2.0", "id": message.get("id"),
                    "result": {"content": [{"type": "text", "text": str(exc)}],
                               "isError": True}}
    if opened is None:
        return _dispatch(None, message, offered, home)
    with opened.lock:
        return _dispatch(opened.session, message, offered, home)


def serve_http(
    project_path: str | None = None,
    scope: str = "research",
    host: str = "127.0.0.1",
    port: int = 8421,
    token: str | None = None,
    doors: Sequence[str] = ("mcp",),
    workspace: Any = None,
    start_in: str | None = None,
) -> int:
    """Serve the doors asked for, over HTTP, until interrupted."""
    import sys

    import uvicorn

    token = token or os.environ.get("QANAT_MCP_TOKEN") or None
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not token:
        print(
            f"qanat serve: refusing to serve {host} with no token. Anything that can reach "
            f"this address could author and run against your project. Pass --token or set "
            f"QANAT_MCP_TOKEN.",
            file=sys.stderr,
        )
        return 2

    try:
        named = [] if host in ("0.0.0.0", "::", "") else [host]
        app = create_app(project_path, scope, token=token, allow_hosts=named, doors=doors,
                         serving=f"http://{host}:{port}", workspace=workspace,
                         start_in=start_in)
    except ScopeError as exc:
        print(f"qanat serve: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"qanat serve: {exc}", file=sys.stderr)
        return 1

    ws = app.state.qanat_workspace
    name = (ws.pinned.name if ws.pinned else
            f"home {ws.home} · " + (f"opened {start_in}" if start_in else
                                    f"{len(ws.names())} project(s)"))
    where = []
    if "mcp" in doors:
        where.append(f"http://{host}:{port}/mcp · scope {scope} · "
                     f"{len(tools_for(scope))} of {len(TOOLS)} tools")
    if "app" in doors:
        where.append(f"pages at http://{host}:{port}/")
    print(
        f"qanat serve: {name} · " + " · ".join(where)
        + ("" if token else " · no token (loopback only)"),
        file=sys.stderr,
    )
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        # **This path never closed the store.** `serve_stdio` closes its session
        # in a `finally` and the clock path closes in one too; here the return
        # followed `uvicorn.run` and nothing let go. It showed up as a
        # `.qanat-lock` surviving a Ctrl-C, which would then be naming a pid that
        # is gone -- harmless, because the note is advisory, and still wrong.
        app.state.qanat_workspace.close_all()
    return 0


__all__ = ["IDLE_SECONDS", "PROTOCOL", "Sessions", "create_app", "serve_http"]
