"""Running the agent CLI the person already has, headless.

The console used this for its Ask box and the console is gone. What is left is
the unattended pass in `research.py`, which drives an installed CLI to try to
break a strategy overnight and records every attempt.

The module is called `headless` and not `agent` because the filename was making
a claim. Qanat is an MCP server. It does not ship an agent, and a file called
`agent.py` sitting in the package told everyone who opened the repo otherwise.

**How it reaches the project.** A DuckDB file takes one writer and `qanat serve`
is holding it, so a second `qanat mcp` cannot open the store. The pass is pointed
at the MCP endpoint that same process serves, at the `research` scope. The scope
is what stops the pass editing the strategy it is meant to be attacking. That
used to be a sentence in a prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The CLIs we know how to drive headless. Each one is already signed in as
#: whoever installed it; none of them needs anything from us.
#:
#: `support` is what this file can actually do with one, and it is not a marketing
#: field. A list that offers a choice which quietly returns nothing is worse than a
#: list of one, so the console shows this and says so.
#:
#: **Why this is one entry.** `cursor-agent` was here and did not work. It was
#: given only `--print`, whose default format is plain text, while the parse loop
#: below reads line-delimited JSON -- so every answer was dropped on the floor and
#: the console showed a finished question with nothing in it. It also never got the
#: tool fence, which is Claude-only flags, so it ran unrestricted.
#:
#: Fixing the format alone would not have been enough: `cursor-agent` refuses to
#: start in a directory until Workspace Trust has been granted interactively, which
#: a headless pass in a fresh project cannot do.
#:
#: Adding another CLI means three things, not one: the argv it needs, a mapping from
#: its event stream onto the `stream_event` / `assistant` / `result` shapes the loop
#: below understands, and its own way of restricting tools. Until all three exist
#: for a given binary, leaving it out is the honest option.
CLIS = (
    {"bin": "claude", "label": "Claude Code", "support": "full", "note": ""},
)


def list_clis(prefer: str = "") -> list[dict[str, Any]]:
    """Every CLI we know about, whether it is installed, and which one would run.

    The console draws from this rather than from `find_cli`, so a person with two
    installed can see both and pick, and a person with none can see the names to
    go and install.
    """
    out: list[dict[str, Any]] = []
    for cli in CLIS:
        path = shutil.which(cli["bin"])
        out.append({**cli, "found": bool(path), "path": path or "", "active": False})
    chosen = find_cli(prefer)
    if chosen:
        for row in out:
            row["active"] = row["bin"] == chosen["bin"]
    return out


def find_cli(prefer: str = "") -> dict[str, str] | None:
    """The CLI to drive: the one asked for, else the first on PATH, else None.

    A named-but-missing preference falls through to the search rather than
    failing. The setting is a preference about which of several to use, and a
    project that names one the machine does not have should still answer.
    """
    if prefer:
        for cli in CLIS:
            if cli["bin"] == prefer:
                found = shutil.which(cli["bin"])
                if found:
                    return {**cli, "path": found}
                break
    for cli in CLIS:
        found = shutil.which(cli["bin"])
        if found:
            return {**cli, "path": found}
    return None


def _agent_pref(state: Any) -> str:
    """Which installed CLI the project asked for, if it asked for one."""
    cfg = getattr(state.project, "agent", None)
    return str(getattr(cfg, "cli", "") or "") if cfg else ""


def _mcp_config_file(base: str) -> str:
    """Point the CLI at the MCP endpoint `qanat serve` is already offering."""
    import tempfile

    token = os.environ.get("QANAT_MCP_TOKEN", "")
    server: dict[str, Any] = {"type": "http", "url": base.rstrip("/") + "/mcp"}
    if token:
        server["headers"] = {"Authorization": f"Bearer {token}"}
    fd, path = tempfile.mkstemp(prefix="qanat-mcp-", suffix=".json")
    with os.fdopen(fd, "w") as fh:
        json.dump({"mcpServers": {"qanat": server}}, fh)
    return path


def _brief(question: str, carried: str = "") -> str:
    """What the agent is told. The tools come from MCP, so they are not listed.

    The old brief spelled out a dozen curl endpoints because the console's HTTP
    API was the only door. MCP hands the agent its own tool list with schemas, so
    repeating them here would be a second copy to keep in step with the first.

    What is left is what MCP cannot say: what this project is, what the pass is
    for, and that an attempt nobody wrote down did not happen.
    """
    carry = f"\n\nEarlier in this session:\n{carried}\n" if carried else ""
    return f"""You are working inside a qanat project. qanat builds trading strategies
as a pipeline of tables and replays them over history.

Your tools reach this project directly. Use them. Do not run the `qanat` CLI and
do not start another `qanat mcp`: this project's store is already open in the
process serving those tools, and a second one cannot have it.

You are connected at the `research` scope. You can read the data, run a replay,
read what it earned, and compare runs. You cannot edit a strategy, and that is
deliberate: a pass that improves the thing it was measuring has measured nothing.

**Record the attempt, not only the result.** After every replay call
`record_trial` with its `run_id` and a one-line `hypothesis` saying what you were
testing, and `parent_run_id` when it varies an earlier run. Do this whether it
worked or not, and especially when it did not. What was tried and failed is the
count that makes a surviving number mean anything, and it is the one thing nobody
writes down.

Read `read_bar` before calling any result good. The same figure means opposite
things at one attempt and at fifty. You cannot move the bar from here. If a
result does not clear it, say so.{carry}

{question}"""


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
    #: What the CLI reported about its own run. All of it arrives on the stream
    #: already and used to be dropped on the floor: the session is what makes
    #: `--resume` possible, the model is part of what produced any number the
    #: agent writes down, and the cost is the only way an unattended pass can be
    #: given a budget.
    session_id: str = ""
    model: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
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
            "session_id": self.session_id,
            "model": self.model,
            "cost_usd": self.cost_usd,
            "turns": self.turns,
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


def carry(summary: str) -> str:
    """A past session's conclusion, for a brief that could not resume it.

    Resuming is better: the agent gets its own transcript back and costs a cache
    read. But `--resume` needs the CLI to still hold that session on disk, and it
    will not after a cleared cache or on another machine -- at which point the
    session row is still here and the conversation is not. This is the degraded
    path, and it is the reason the summary is worth writing even for a list
    nobody reads.
    """
    if not summary:
        return ""
    return ("Earlier in this session, before its transcript was lost:\n"
            f"{summary.strip()}\n\n")


#: What a session gets asked once it is over. Deliberately narrow, and deliberately
#: allowed to say that nothing happened -- a history where every row claims a
#: finding is a history nobody trusts, and most sessions are a question and an
#: answer. The prohibitions are the load-bearing part: a summary that records a
#: single failed replay as "momentum does not work here" will be quoted back for
#: months, long after the reason was a typo in a lookback.
SUMMARY_BRIEF = """Summarise this session in at most two sentences, for someone
scanning a list of past sessions to find this one again.

Say what was being worked on and what came of it. Name any alpha or table that
was added or changed, and any replay that was run, with its net.

Do NOT write a conclusion the session did not reach. One replay that lost money
is a result for that configuration, not evidence the idea is wrong -- say what was
run and what it returned, never "X does not work". If nothing was built, changed
or replayed, say exactly: nothing was changed.

Reply with the summary and nothing else. No preamble, no heading, no quotes."""


def summarise(session_id: str, root: Path, prefer: str = "",
              timeout: float = 120.0) -> str:
    """Ask the session to describe itself, by resuming it.

    Resuming rather than replaying a transcript we assembled: the CLI still holds
    the conversation, so this costs a cache read and the summary is written by the
    thing that was actually there.

    Returns "" on any failure. A session without a summary is a smaller problem
    than an ask that raised while tidying up after itself.
    """
    cli = find_cli(prefer)
    if not cli or cli["bin"] != "claude" or not session_id:
        return ""
    cmd = [cli["path"], "-p", SUMMARY_BRIEF, "--resume", session_id,
           "--disallowedTools",
           "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,WebFetch,WebSearch"]
    try:
        done = subprocess.run(cmd, cwd=str(root), capture_output=True, text=True,
                              timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    if done.returncode not in (0, None):
        return ""
    return (done.stdout or "").strip()[:600]


def run(ask: Ask, root: Path, base: str, timeout: float = 180.0,
        prefer: str = "", session_id: str = "", resume: bool = False,
        carried: str = "") -> None:
    """Drive the CLI headless against this project's MCP server.

    `base` is where `qanat serve` is listening. The pass reaches the project
    through the MCP endpoint there, because that process is the one holding the
    store open.
    """
    cli = find_cli(prefer)
    if not cli:
        ask.error = ("No agent CLI found on this machine. Install Claude Code or Cursor "
                     "and sign in, then ask again. qanat never holds a key of its own.")
        ask.done = True
        return

    ask.cli = cli["label"]
    _say(ask, "start", f"asking {cli['label']}")
    mcp_config = _mcp_config_file(base)

    cmd = [cli["path"], "-p", _brief(ask.question, carried)]
    if cli["bin"] == "claude":
        #  We mint the id and hand it over, rather than reading back whichever one
        #  the CLI generated. One value then names the same conversation on both
        #  sides -- and it exists before the process does, so the row is written
        #  and joinable even if this run never finishes.
        if session_id:
            cmd += ["--resume", session_id] if resume else ["--session-id", session_id]
            ask.session_id = session_id
        cmd += ["--output-format", "stream-json", "--verbose",
                # Without this the answer arrives in one piece when the process
                # ends: thirty seconds of a spinner, then a wall of text. With it
                # the reply is readable while it is being written, which is the
                # difference between waiting for an answer and watching one.
                "--include-partial-messages",
                # The project is reached over MCP and nowhere else. The fence used
                # to be "Bash only, for curl", which is a wide door: asked to
                # reshape some ideas, this agent walked out of the project into
                # qanat's own installed source and then into `~/.claude/projects`.
                # Now it holds the tools its scope offers and nothing besides.
                "--mcp-config", mcp_config,
                "--strict-mcp-config",
                "--allowedTools", "mcp__qanat",
                "--disallowedTools",
                "Bash,Read,Glob,Grep,Edit,Write,NotebookEdit,WebFetch,WebSearch,Task,ToolSearch"]
    else:
        #  Unreachable while CLIS holds one entry, and kept deliberately: it is the
        #  seam a second CLI would be added at, and it says out loud that plain
        #  `--print` is not enough -- the loop below reads JSON lines.
        ask.error = (f"{cli['label']} is listed but this build cannot drive it: it "
                     "needs an argv, an event mapping and a tool fence of its own")
        ask.done = True
        return

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
            # Every event carries the session, including the first one, so this is
            # set long before the run ends -- which is what lets a stop or a crash
            # still leave something resumable behind.
            if not ask.session_id and ev.get("session_id"):
                ask.session_id = str(ev["session_id"])
            if ev.get("type") == "system":
                # The init frame says which model is about to answer. Only that
                # one carries it; the hook frames share the type and do not.
                if ev.get("subtype") == "init" and ev.get("model"):
                    ask.model = str(ev["model"])
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
                # What the run cost, from the CLI's own accounting rather than a
                # guess of ours.
                try:
                    ask.cost_usd = float(ev.get("total_cost_usd") or 0.0)
                    ask.turns = int(ev.get("num_turns") or 0)
                except (TypeError, ValueError):
                    pass
                if isinstance(ev.get("usage"), dict):
                    ask.usage = ev["usage"]
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
