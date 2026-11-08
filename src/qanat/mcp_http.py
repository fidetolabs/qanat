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

**One process serves one project.** Every session on this server shares a
single store behind a lock. That is a real limit, not a simplification: serving
two projects means two processes, and serving many tenants means a process and a
store apiece. Written down in `docs/agents.md` rather than implied away.

**A non-loopback bind needs a token.** Binding to an address the world can reach
and then answering anybody is not a default worth having, so it is refused.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections.abc import Sequence
from typing import Any

import anyio
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from qanat import __version__
from qanat.mcp import PROTOCOL, TOOLS, ScopeError, Session, _dispatch, rank, tools_for

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

    def __init__(self) -> None:
        self._seen: dict[str, float] = {}

    def open(self) -> str:
        self.sweep()
        if len(self._seen) >= MAX_SESSIONS:
            oldest = min(self._seen, key=lambda k: self._seen[k])
            self._seen.pop(oldest, None)
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
        return self._seen.pop(sid, None) is not None

    def sweep(self) -> None:
        cutoff = time.monotonic() - IDLE_SECONDS
        for sid in [s for s, t in self._seen.items() if t < cutoff]:
            del self._seen[sid]

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
):
    """A FastAPI app carrying the doors it is asked for.

    **`doors` names what gets mounted, and nothing is mounted by default but
    `/mcp`.** `mcp` puts the tool endpoint on; `app` puts the pages and `/api`
    on. Both is one program with two doors, which is the ordinary case for one
    engineer. `/health` is the server answering for itself and is not a door.

    `state` is how a served process hands over a store it has already opened, along
    with the scheduler running against it. Without it this opens the project
    itself, which is the plain `qanat serve` case.
    """
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
    if state is not None:
        # One store, already open, with a scheduler on it. Opening a second one
        # here is what StoreBusy is for.
        session = Session.__new__(Session)
        session.path = str(state.root)
        session.project, session.root = state.project, state.root
        session._store = state.store
        # So a tool can say whether a clock is attached rather than assume one.
        session.state = state
    else:
        session = Session(project_path)
        # And take the writer lock, so a store held by another program is reported
        # here rather than on the first write a door takes. Opening alone does not
        # take it: `meta.duckdb` is attached lazily, by design, so that a reader
        # never has to.
        session.store.take_writer_lock()

    sessions = Sessions()
    allowed = allowed_hosts(allow_hosts)
    # One store, one writer. Tool calls are blocking and run on worker threads, so
    # without this two requests would be inside the store at once. It belongs to
    # this app rather than to the module: two apps in one process (which is what
    # the tests do) are two projects and must not share a lock.
    store_lock = threading.Lock()

    app = FastAPI(
        title=f"qanat · {session.project.name}",
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

        replies: list[dict] = []
        for one in messages:
            if not isinstance(one, dict):
                replies.append(_error(-32600, "not a JSON-RPC message"))
                continue
            # The tool list is the scope's, not the whole shelf. `_dispatch` reads
            # it for both `tools/list` and `tools/call`, so pinning it here pins
            # both.
            reply = await anyio.to_thread.run_sync(_call, session, one, offered, store_lock)
            if reply is not None:
                replies.append(reply)

        if not replies:
            # Notifications only. Nothing to answer with, and saying so is not an
            # error -- `notifications/initialized` arrives on every handshake.
            return Response(status_code=202)

        headers = {}
        if starting:
            headers["Mcp-Session-Id"] = sessions.open()
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
            "project": session.project.name,
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
        # The pages and their API, over the same session and the same lock. A
        # second door onto the service layer, not onto `/mcp`.
        from qanat.app import mount

        mount(app, session, store_lock, scope)

    app.state.qanat_session = session
    app.state.scope = scope
    app.state.qanat_state = state
    app.state.doors = tuple(doors)
    return app


def _call(
    session: Session, message: dict, offered: list[dict], store_lock: threading.Lock
) -> dict | None:
    """Run one message against the store, one at a time."""
    with store_lock:
        return _dispatch(session, message, offered)


def serve_http(
    project_path: str | None = None,
    scope: str = "research",
    host: str = "127.0.0.1",
    port: int = 8421,
    token: str | None = None,
    doors: Sequence[str] = ("mcp",),
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
        app = create_app(project_path, scope, token=token, allow_hosts=named, doors=doors)
    except ScopeError as exc:
        print(f"qanat serve: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"qanat serve: {exc}", file=sys.stderr)
        return 1

    name = app.state.qanat_session.project.name
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
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


__all__ = ["IDLE_SECONDS", "PROTOCOL", "Sessions", "create_app", "serve_http"]
