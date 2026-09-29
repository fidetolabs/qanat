"""The same tools, reached over HTTP instead of a pipe.

`qanat mcp` speaks JSON-RPC on stdin and stdout, which is the right transport for
one person on one machine and cannot be the hosted one: a request arriving at a
server has nowhere to keep a child process, and the client is somewhere else
entirely. This module is the other end -- MCP's Streamable HTTP transport over
the same `_dispatch`, the same tools, and the same scopes.

Three things are deliberately fixed here rather than left to the caller.

**The scope is pinned at startup.** A caller that names its own scope has no
scope at all, and D-20260929-04 makes each one a published contract. The flag
that starts the server decides; nothing in a request can widen it.

**One process serves one project.** A DuckDB file takes one writer, so every
session on this server shares a single store behind a lock. That is a real
limit, not a simplification: serving two projects means two processes, and
serving many tenants means Postgres and a store apiece. Written down in
`docs/agents.md` rather than implied away.

**A non-loopback bind needs a token.** Binding to an address the world can reach
and then answering anybody is not a default worth having, so it is refused.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from typing import Any

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from qanat import __version__
from qanat.mcp import PROTOCOL, TOOLS, ScopeError, Session, _dispatch, rank, tools_for

# FastAPI resolves a route's annotations against this module's globals, and
# `from __future__ import annotations` turns them into strings -- so `Request`
# has to be importable from here or every route reads it as a query parameter.
# Importing web things at module scope is safe because nothing imports this
# module unless `--http` was asked for.

#: The names this server may be reached by.
#:
#: At `full` this server writes step scripts and runs them, so anything that can
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
    if v.startswith("["):               # [::1]:8420 -> ::1
        end = v.find("]")
        return v[1:end] if end > 0 else v
    head, sep, tail = v.rpartition(":")
    return head if sep and tail.isdigit() else v


def allowed_hosts(extra: list[str] | None = None) -> set[str]:
    """Loopback, plus whatever `QANAT_ALLOWED_HOSTS` and the caller add."""
    import os

    names = {h.lower() for h in LOOPBACK_HOSTS}
    for source in (os.environ.get("QANAT_ALLOWED_HOSTS", "").split(","), extra or []):
        names |= {h.strip().lower() for h in source if h and h.strip()}
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
):
    """A FastAPI app that answers MCP on one endpoint.

    `state` is how `qanat serve` hands over a store it has already opened, along
    with the scheduler running against it. Without it this opens the project
    itself, which is the plain `qanat mcp --http` case.
    """
    from qanat.store import set_actor

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
    else:
        session = Session(project_path)
        _ = session.store  # open now, so a busy database is reported at startup

    sessions = Sessions()
    allowed = allowed_hosts(allow_hosts)
    # One store, one writer. Tool calls are blocking and run on worker threads, so
    # without this two requests would be inside the store at once. It belongs to
    # this app rather than to the module: two apps in one process (which is what
    # the tests do) are two projects and must not share a lock.
    store_lock = threading.Lock()

    app = FastAPI(
        title=f"qanat mcp · {session.project.name}",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):  # type: ignore[no-untyped-def]
        """Refuse a name we do not answer to, and a caller without the token.

        The `Host` check is the same one the console uses and it is here for the
        same reason: binding to loopback does not stop a page the person is
        visiting from pointing its own domain at 127.0.0.1 and calling this
        server believing it is same-origin. CORS never enters into it. The name
        the browser asked for does, and it arrives in `Host`.
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
        if token:
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

    @app.post("/mcp")
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

    @app.get("/mcp")
    async def get_mcp():  # type: ignore[no-untyped-def]
        """No server-initiated messages, so no stream to open.

        The spec allows a server to answer 405 here, and answering it honestly is
        better than holding a connection open that will never carry anything.
        """
        return JSONResponse(
            status_code=405,
            content={"detail": "this server sends nothing on its own; POST to /mcp."},
        )

    @app.delete("/mcp")
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

    app.state.qanat_session = session
    app.state.scope = scope
    app.state.qanat_state = state
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
) -> int:
    """Serve MCP over HTTP until interrupted."""
    import sys

    import uvicorn

    token = token or os.environ.get("QANAT_MCP_TOKEN") or None
    loopback = host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not token:
        print(
            f"qanat mcp: refusing to serve {host} with no token. Anything that can reach "
            f"this address could author and run against your project. Pass --token or set "
            f"QANAT_MCP_TOKEN.",
            file=sys.stderr,
        )
        return 2

    try:
        named = [] if host in ("0.0.0.0", "::", "") else [host]
        app = create_app(project_path, scope, token=token, allow_hosts=named)
    except ScopeError as exc:
        print(f"qanat mcp: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"qanat mcp: {exc}", file=sys.stderr)
        return 1

    name = app.state.qanat_session.project.name
    print(
        f"qanat mcp: {name} · scope {scope} · {len(tools_for(scope))} of {len(TOOLS)} tools "
        f"· http://{host}:{port}/mcp{'' if token else ' · no token (loopback only)'}",
        file=sys.stderr,
    )
    uvicorn.run(app, host=host, port=port, log_level="warning")
    return 0


__all__ = ["IDLE_SECONDS", "PROTOCOL", "Sessions", "create_app", "serve_http"]
