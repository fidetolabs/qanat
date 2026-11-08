"""The app door: pages and a JSON API for the one engineer running this Qanat.

**A second adapter over the same service layer, and it holds no logic.** Every
route reads its arguments, calls one function in `filings`, `metrics`, `screen`,
`filters`, `reading` or `catalog`, and returns the answer. A page needing
something no function gives gets that function in the service layer first --
`catalog.progress` is the one so far -- so the two doors cannot drift.

**Neither door calls the other.** The MCP surface is short on purpose, because
every tool definition is sent on every request; a page's need is not a reason for
a tool. And MCP over HTTP to here would be a hop and a second auth for nothing.

**The scope still holds.** A write route is allowed exactly when the tool doing
the same write is offered at the scope this server was started with, so a
server pinned at `research` serves pages that read and refuses the ones that
write. One person, no login: a scope is a capability, not an identity.

Started with `qanat mcp --http --app`, in the same process and behind the same
Host and token guard as `/mcp`.
"""

from __future__ import annotations

from qanat.app.api import mount

__all__ = ["mount"]
