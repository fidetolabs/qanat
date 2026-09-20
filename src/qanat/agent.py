"""Asking in plain English, using the agent the person already has.

The console never holds a key and never signs anyone in. It runs the agent CLI
already installed on the machine -- Claude Code, Cursor -- in headless mode, and
that CLI is already authenticated. Nothing to paste, nothing to bill, and the
credential stays where the person put it.

**Why the agent talks HTTP and not MCP.** A DuckDB file takes one writer, and the
console is holding it. A second `qanat mcp` in the same project cannot open the
store, which is what `StoreBusy` says. So the agent is pointed at the console's
own API instead: the same service layer the MCP tools sit on, reached over the
loopback port that is already serving. One process, one writer, no lock.

That endpoint is worth reaching only from this machine, which is what the `Host`
guard in `api.py` is for -- without it any page in any tab could start an agent
run here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The CLIs we know how to drive headless, best first. Each one is already signed
#: in as whoever installed it; none of them needs anything from us.
CLIS = (
    {"bin": "claude", "label": "Claude Code"},
    {"bin": "cursor-agent", "label": "Cursor"},
)


def find_cli() -> dict[str, str] | None:
    """The first agent CLI on PATH, or None if the person has not installed one."""
    for cli in CLIS:
        found = shutil.which(cli["bin"])
        if found:
            return {**cli, "path": found}
    return None


def _brief(base: str, question: str) -> str:
    """What the agent is told: the project it is in, and the door into it.

    The API is described rather than wrapped. It is already the service layer the
    console draws from, so an agent that can read it can answer anything the
    console can show, and an agent that can post to it can change anything the
    console can change.
    """
    return f"""You are working inside a qanat project. qanat builds trading
strategies as a pipeline of tables and replays them over history.

The console for THIS project is already running at {base} and is holding the
database open, so do NOT run the `qanat` CLI and do NOT start `qanat mcp` -- both
would fail on a locked store. Use the console's HTTP API with curl instead. It
only answers to 127.0.0.1, which is where you are.

Read:
  GET  {base}/api/graph                 stages, tables, jobs, edges
  GET  {base}/api/project               the whole qanat.yaml, and whether it is valid
  GET  {base}/api/alphas                every strategy with what it earned
  GET  {base}/api/jobs/<id>             one step: from, to, options, and its source
  GET  {base}/api/table/<stage>/<name>  rows, columns and types
  GET  {base}/api/backtests             every replay this project has run
  GET  {base}/api/backtest/conditions   what a replay can cover, and what to ask about

Change:
  POST {base}/api/alphas                add or edit a strategy
  POST {base}/api/steps                 add a step
  POST {base}/api/jobs/<id>/run         run one job now
  POST {base}/api/backtest              replay and price it

  GET  {base}/api/profile/<stage>/<name>  what is in a table: fill, distinct, range
  GET  {base}/api/check                 whether the project holds its contract

Stay inside this project. Everything you need about it is behind that API --
including the source of any step, from `GET /api/jobs/<id>`. Do not read qanat's
own installed source, do not look through the home directory, and do not go
outside the project folder to answer a question about the project. If something
you need is genuinely not reachable through the API, say so rather than going
around it.

**Change only what was asked for.** Read first. If the person asked a question,
answer it -- do not build, edit, or run anything on the way. If they asked for a
change, make that change and no more, and say plainly what you did. "Suggest
some ideas" is a question, not an instruction to write a strategy.

Keep the final reply short, a few sentences at most, in plain English with no
trading jargon the person did not use first. If a table is the clearest answer,
write it as a markdown table -- the console renders those.

The question: {question}"""


@dataclass
class Ask:
    """One question, and what the agent did about it while answering."""

    question: str
    started: float = field(default_factory=time.time)
    lines: list[dict[str, Any]] = field(default_factory=list)
    answer: str = ""
    #: The reply so far, while it is still being written. Cleared once `answer`
    #: holds the whole of it.
    partial: str = ""
    error: str = ""
    done: bool = False
    cli: str = ""
    #: The running CLI, so the person can change their mind. Not in `state()`:
    #: a Popen is not something the console needs to know about.
    proc: Any = None
    stopped: bool = False

    def stop(self) -> bool:
        """Kill the agent mid-answer. True if there was something to kill."""
        if self.done or self.proc is None or self.proc.poll() is not None:
            return False
        self.stopped = True
        try:
            self.proc.kill()
        except Exception:  # noqa: BLE001, S110
            pass
        return True

    def state(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "lines": list(self.lines),
            "answer": self.answer,
            "partial": self.partial,
            "error": self.error,
            "done": self.done,
            "cli": self.cli,
            "elapsed": round(time.time() - self.started, 1),
        }


def _say(ask: Ask, kind: str, text: str, detail: str = "") -> None:
    ask.lines.append({"kind": kind, "text": text, "detail": detail,
                      "at": round(time.time() - ask.started, 1)})


#: What a tool call is doing, in words the person did not have to learn. The
#: agent's own tool names leak its internals; this log is about the project.
def _describe(name: str, args: dict[str, Any]) -> tuple[str, str]:
    cmd = str(args.get("command") or "")
    if name == "Bash" and "curl" in cmd:
        verb = "changing" if any(m in cmd for m in ("-X POST", "POST", "--data", "-d ")) else "reading"
        for part in cmd.split():
            if "/api/" in part:
                path = part.strip("'\"").split("/api/", 1)[1].split("?")[0]
                return ("read" if verb == "reading" else "write", f"{verb} {path}")
        return ("run", f"{verb} the project")
    if name in ("Read", "Glob", "Grep"):
        return ("read", f"reading {Path(str(args.get('file_path') or args.get('pattern') or '')).name}")
    if name in ("Write", "Edit"):
        return ("write", f"editing {Path(str(args.get('file_path') or '')).name}")
    if name == "Bash":
        return ("run", cmd[:70])
    return ("run", name)


def snapshot(graph: dict[str, Any]) -> dict[str, Any]:
    """Enough of the project to say what changed after the agent has been in it."""
    return {
        "rows": {t["ref"]: t.get("rows") or 0 for t in graph.get("tables") or []},
        "jobs": {j["id"]: json.dumps(j.get("options") or {}, sort_keys=True)
                 for j in graph.get("jobs") or []},
    }


def diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """What the agent actually changed, in the project's own terms.

    The tool calls above say what it did; this says what came of it. A person
    watching wants the second one -- a row count that moved, a step that appeared.
    """
    out: list[str] = []
    for job in sorted(set(after["jobs"]) - set(before["jobs"])):
        out.append(f"new step {job}")
    for job in sorted(set(before["jobs"]) & set(after["jobs"])):
        if before["jobs"][job] != after["jobs"][job]:
            out.append(f"{job} settings changed")
    for ref in sorted(set(after["rows"]) - set(before["rows"])):
        out.append(f"new table {ref} · {after['rows'][ref]:,} rows")
    for ref in sorted(set(before["rows"]) & set(after["rows"])):
        was, now = before["rows"][ref], after["rows"][ref]
        if was != now:
            out.append(f"{ref} · {was:,} → {now:,} rows")
    return out


def run(ask: Ask, root: Path, base: str, timeout: float = 180.0) -> None:
    """Drive the CLI headless and turn its stream into lines the console shows."""
    cli = find_cli()
    if not cli:
        ask.error = ("No agent CLI found on this machine. Install Claude Code or Cursor "
                     "and sign in, then ask again. qanat never holds a key of its own.")
        ask.done = True
        return

    ask.cli = cli["label"]
    _say(ask, "start", f"asking {cli['label']}")

    cmd = [cli["path"], "-p", _brief(base, ask.question)]
    if cli["bin"] == "claude":
        cmd += ["--output-format", "stream-json", "--verbose",
                # Without this the answer arrives in one piece when the process
                # ends: thirty seconds of a spinner, then a wall of text. With it
                # the reply is readable while it is being written, which is the
                # difference between waiting for an answer and watching one.
                "--include-partial-messages",
                # Bash only, and only really for curl. Everything about the project
                # is behind the API now -- a step's source included -- so nothing
                # here needs to read or write a file. Asked to reshape some ideas,
                # this agent went from the project into qanat's own installed
                # source and then into `~/.claude/projects`; a console whose front
                # door is a chat box cannot leave that door that wide.
                "--allowedTools", "Bash",
                "--disallowedTools", "Read,Glob,Grep,Edit,Write,NotebookEdit,WebFetch,WebSearch"]
    else:
        cmd += ["--print"]

    try:
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
        )
    except Exception as exc:  # noqa: BLE001
        ask.error = f"could not start {cli['label']}: {exc}"
        ask.done = True
        return
    ask.proc = proc

    def reap() -> None:
        time.sleep(timeout)
        if proc.poll() is None:
            proc.kill()
            ask.error = f"{cli['label']} took longer than {int(timeout)}s and was stopped"

    threading.Thread(target=reap, daemon=True).start()

    try:
        for raw in proc.stdout or []:
            raw = raw.strip()
            if not raw or not raw.startswith("{"):
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            # The reply as it is being written. `result` at the end is the same
            # text, complete -- this is only so the console has something to show
            # before then.
            if ev.get("type") == "stream_event":
                se = ev.get("event") or {}
                if se.get("type") == "content_block_delta":
                    d = se.get("delta") or {}
                    if d.get("type") == "text_delta":
                        ask.partial += str(d.get("text") or "")
                elif se.get("type") == "content_block_start":
                    blk = se.get("content_block") or {}
                    # a tool call interrupts the prose: keep the paragraphs apart
                    if blk.get("type") == "tool_use" and ask.partial:
                        ask.partial += "\n\n"
                continue
            if ev.get("type") == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "tool_use":
                        kind, text = _describe(block.get("name", ""), block.get("input") or {})
                        _say(ask, kind, text)
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        _say(ask, "think", block["text"].strip()[:160])
            elif ev.get("type") == "result":
                ask.answer = str(ev.get("result") or "").strip()
                ask.partial = ""
    finally:
        proc.wait()
        if ask.stopped:
            # A stop is a decision, not a failure. Whatever it had already done is
            # still on the project, so the lines above it stay.
            ask.error = ask.error or "stopped before it finished"
            _say(ask, "done", "stopped")
        else:
            if proc.returncode not in (0, None) and not ask.answer and not ask.error:
                ask.error = (proc.stderr.read() if proc.stderr else "").strip()[:400] or \
                            f"{ask.cli} exited {proc.returncode}"
            _say(ask, "done", "finished")
        # Not `done` yet: the caller still has to work out what changed, and the
        # console stops polling the moment this flips.
