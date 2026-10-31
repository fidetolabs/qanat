"""qanat -- the command line. Four verbs, and each one is here for a reason.

    qanat init [dir]     scaffold a project that runs green with no keys
    qanat check          hold the pipeline against the stage contract
    qanat mcp            serve this project to an agent, over stdio or --http
    qanat schedule       run the clock, and serve nothing

**This is reached by an agent, so most of what was here was the wrong door.**
`ls`, `graph`, `plan`, `run`, `screen` and `prune` were a person's way into a
thing whose caller is a program, and each one was a second implementation of a
question a tool already answers -- a second place the behaviour could quietly
differ. They are gone, each named below beside what replaced it:

    ls        list_tables, list_steps, describe_table -- it was those three in one dump
    graph     nothing. A terminal picture for a person, and its own markers
              disagreed with its legend
    plan      the `plan` tool
    prune     the `plan` tool's `prune` flag. The one irreversible verb does not
              belong at the top level, and folding it in means you see the list
              from the same call that acts on it
    run       the `run` tool
    screen    the `screen` tool

**What stays, stays because there is nothing to ask.** `init` runs before a
project exists. `mcp` is the transport. `check` is deliberate: somebody
connecting for the first time has to be able to tell a broken project from a
broken server, and a tool cannot answer that when the tool is what is broken.

**And the clock and the server are two commands, not one with a flag.** They
used to be one process -- scheduler and MCP server together, with
`--no-schedule` to turn the clock off and no way at all to run the clock alone.
That is backwards for the two deployments that exist: a recorder wants a clock
and no server, and a hosted reader wants a server over a store somebody else
fills. `qanat schedule` is the clock; `qanat mcp --http` is the server;
`qanat mcp --http --schedule` is both, for one machine doing both jobs.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from qanat import __version__
from qanat.mcp import DEFAULT_SCOPE, SCOPES  # for --scope's choices

_TTY = sys.stdout.isatty()
G, R_, Y, B, D, X = (
    ("\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[2m", "\033[0m")
    if _TTY
    else ("", "", "", "", "", "")
)


def c(text: str, colour: str) -> str:
    return f"{colour}{text}{X}" if _TTY else text


def _load(args):
    from qanat.project import load

    return load(getattr(args, "project", None))


#: What each removed command was replaced by. `qanat <name>` prints this rather
#: than dying in argparse, because somebody has these in a script.
#:
#: **`graph` is named as having none**, which is the honest entry: it drew a
#: picture for a person out of a thing whose caller is a program, and its own
#: markers disagreed with its legend -- `○ 4 rows` for a table `ls` drew as `●`.
MOVED = {
    "ls": "the list_tables, list_steps and describe_table tools. `ls` was those "
          "three in one dump",
    "graph": "nothing. It drew the pipeline for a person to look at, and this is "
             "reached by an agent",
    "plan": "the `plan` tool",
    "prune": "the `plan` tool's `prune` flag, so you see the orphans from the same "
             "call that drops them",
    "run": "the `run` tool",
    "screen": "the `screen` tool",
    "serve": "`qanat mcp --http --schedule` for both, or `qanat schedule` for the "
             "clock alone and `qanat mcp --http` for the server alone",
}


#: Global options that take a value, so their value is not mistaken for the verb.
#: `qanat -p ./proj graph` puts a path in front of the command, and reading the
#: first non-flag word would have read the path.
_TAKES_VALUE = frozenset({"-p", "--project"})


def _verb(argv: list[str]) -> str | None:
    """The subcommand in an argv, skipping the global options and their values."""
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _TAKES_VALUE:
            i += 2
            continue
        if arg.startswith("-"):          # a flag, or --project=X
            i += 1
            continue
        return arg
    return None


def _say_it_moved(argv: list[str]) -> int | None:
    """If the verb is a command that went, say where. Otherwise None.

    Checked before argparse rather than registered as a hidden subparser: a
    subparser with `help=SUPPRESS` still appears in the choices line, and the
    point of the trim is that `--help` lists four verbs.
    """
    word = _verb(argv)
    if word not in MOVED:
        return None
    print(f"qanat {word} is gone. Use {MOVED[word]}.", file=sys.stderr)
    print("`qanat mcp` serves the tools; see docs/agents.md.", file=sys.stderr)
    return 2


# ------------------------------------------------------------------------ init
def cmd_init(args) -> int:
    from qanat.scaffold import write_project

    target = Path(args.directory).resolve()
    name = args.name or target.name.replace("-", "_")
    target.mkdir(parents=True, exist_ok=True)
    written = write_project(target, name, force=args.force, store=args.store)
    if not written:
        print(c("nothing written -- files already exist. Use --force to overwrite.", Y))
        return 1
    for p in written:
        print(f"  {c('+', G)} {p.relative_to(target)}")

    if not args.demo:
        print()
        if target != Path.cwd():
            print(f"  cd {args.directory}")
        print("  qanat check                # hold it against the contract")
        print("  qanat schedule --run-now   # fetch and parse, then keep a clock")
        print("  qanat mcp                  # hand it to an agent\n")
        return 0

    print(f"\n{c('fetching and parsing', D)}")
    if (rc := _demo_fill(target)) != 0:
        return rc
    where = "" if target == Path.cwd() else f"cd {args.directory} && "
    print(f"\n{where}qanat mcp --scope research")
    return 0


def _demo_fill(target: Path) -> int:
    """Run the pipeline once, so the first thing an agent is asked about has real
    rows in it rather than empty tables.

    This used to also price every weights table and print what each basket
    earned. Nothing in the package simulates holding a position any more, so
    what a new project has to show for itself is the facts it read.
    """
    from qanat.project import load
    from qanat.runner import run_all
    from qanat.store import Store

    project, root = load(target)
    store = Store(project.store_url(root))
    try:
        failed = []
        for r in run_all(store, project, root):
            if r.ok:
                print(f"  {c('·', D)} {r.job_id:<26} {c(f'{r.rows:,} rows', G)}")
            else:
                failed.append(r)
        for r in failed:
            print(f"  {c('error', R_)} {r.job_id}: {r.error}")
        return 1 if failed else 0
    finally:
        store.close()


# ----------------------------------------------------------------------- check
def cmd_check(args) -> int:
    from qanat.project import validate

    project, root = _load(args)
    rep = validate(project, root)
    for w in rep.warnings:
        print(f"  {c('warn', Y)}  {w}")
    for e in rep.errors:
        print(f"  {c('error', R_)} {e}")
    print()
    if rep.ok:
        print(c(f"✓ {project.name}: {len(project.stages)} stages, "
                f"{len(project.tables())} tables, "
                f"{len(project.sources) + len(project.steps)} jobs · contract holds", G))
        return 0
    print(c(f"✗ {len(rep.errors)} error(s)", R_))
    return 1


# -------------------------------------------------------------- open a project
def _opened(args):
    """Load, hold against the contract, repair a half-finished screen, report drift.

    Shared by the two long-running commands, because both of them have to do all
    four before they are any use, and doing three of them is a process that looks
    healthy and is not.
    """
    from qanat.plan import plan as plan_project
    from qanat.project import validate
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    for e in rep.errors:
        print(f"  {c('error', R_)} {e}")
    if not rep.ok and not args.force:
        print(c("refusing to run a pipeline that breaks the contract. --force to override.",
                R_))
        return None

    store = Store(project.store_url(root))
    _repair_after_crash(store, project, root)
    for ch in plan_project(project, root, store).changes:
        print(f"  {c('drift', Y)} {ch.target} · "
              f"{ch.note or (ch.details[0] if ch.details else '')}")
    return project, root, store


# -------------------------------------------------------------------- schedule
def cmd_schedule(args) -> int:
    """The clock, and nothing served.

    This is what CI or systemd invokes, and it is deliberately not what a person
    types. It used to be unreachable: the only command that started a scheduler
    served an MCP server alongside it, so a recorder that wanted a clock and no
    open port had nothing to run.
    """
    from qanat.scheduler import Scheduler, sleep_forever

    opened = _opened(args)
    if opened is None:
        return 1
    project, root, store = opened

    sched = Scheduler(store, project, root, workers=args.workers)
    print(f"\n  {c('qanat', B)} {D}v{__version__}{X}  {project.name}")
    print(f"  schedule {c(f'{len(sched._jobs)} job(s) on a clock', B)} "
          f"{D}· nothing is served from this process{X}")
    print(f"  store    {D}{project.store_url(root)}{X}\n")
    sys.stdout.flush()

    sched.start()
    if args.run_now:
        # One full pass in dependency order. Firing every job at once would only
        # make the downstream steps fail on a table that is not there yet.
        import threading

        from qanat.runner import run_all

        threading.Thread(target=run_all, args=(store, project, root), daemon=True).start()
    try:
        sleep_forever()
    finally:
        sched.stop()
        store.close()
    return 0


def _repair_after_crash(store, project, root) -> None:
    """Rebuild what a screen that never finished left truncated.

    A screen rewrites every derived table at each as-of date and puts them back when
    it ends. A SIGKILL, an OOM kill or a closed laptop skips that, and the store then
    opens on tables holding only the rows that existed at some date in the past --
    with nothing to say so. The raw tables are untouched, so one ordinary pass is the
    whole cure; it just has to be run.
    """
    if not getattr(store, "interrupted_screen", None):
        return
    from qanat.runner import run_all

    print(c(f"  a screen was interrupted at as-of {store.interrupted_screen}", Y))
    print(f"  {D}the tables it rewrote hold only the rows from then. Rebuilding from raw…{X}")
    failed = [r for r in run_all(store, project, root, sources=False) if not r.ok]
    for r in failed:
        print(f"  {c('error', R_)} {r.job_id}: {r.error}")
    if not failed:
        print(f"  {c('ok', G)} rebuilt\n")


# ------------------------------------------------------------------------- mcp
def cmd_mcp(args) -> int:
    """Serve this project to an agent. A clock only if one is asked for.

    `--schedule` is what `serve` used to be, and it is a flag here rather than a
    command of its own because the server is the thing being started and the
    clock is a property of this process. Without it, nothing in this process
    reads a cron line -- which `list_subscriptions` reports rather than hides.
    """
    if getattr(args, "read_only", False):
        print("qanat mcp: --read-only is gone. It split 33 tools into 23 and 10, which is "
              "too blunt to be a promise. Use --scope data to read tables, or --scope "
              "research to add screens. See docs/agents.md.", file=sys.stderr)
        return 2

    if not args.http:
        if args.schedule:
            print("qanat mcp: --schedule needs --http. Over stdio this server is your "
                  "client's child, so a clock started here dies with the conversation. "
                  "Run `qanat schedule` beside it instead.", file=sys.stderr)
            return 2
        from qanat.mcp import serve_stdio

        return serve_stdio(getattr(args, "project", None), scope=args.scope)

    if not args.schedule:
        from qanat.mcp_http import serve_http

        return serve_http(getattr(args, "project", None), scope=args.scope,
                          host=args.host, port=args.port, token=args.token)

    return _serve_with_clock(args)


def _serve_with_clock(args) -> int:
    """MCP over HTTP with a scheduler in the same process.

    One machine doing both jobs. The store is opened once and handed to both, so
    there is one writer rather than two processes contending for it.
    """
    import uvicorn

    from qanat.mcp_http import create_app
    from qanat.runtime import AppState
    from qanat.scheduler import Scheduler

    opened = _opened(args)
    if opened is None:
        return 1
    project, root, store = opened

    # Binding to an address on purpose means meaning to be reached at it, so
    # that name is allowed. A wildcard bind names nothing, and never arrives in
    # `Host` anyway -- serving under a real hostname stays a deliberate setting.
    named = [] if args.host in ("0.0.0.0", "::", "") else [args.host]
    token = args.token or os.environ.get("QANAT_MCP_TOKEN") or None
    if args.host not in ("127.0.0.1", "::1", "localhost") and not token:
        print(c(f"refusing to serve {args.host} with no token. Pass --token or set "
                f"QANAT_MCP_TOKEN.", R_))
        return 2

    sched = Scheduler(store, project, root, workers=args.workers)
    state = AppState(store=store, project=project, root=root, sched=sched)
    app = create_app(None, args.scope, token=token, allow_hosts=named, state=state)
    sched.start()
    if args.run_now:
        import threading

        from qanat.runner import run_all

        threading.Thread(target=run_all, args=(store, project, root), daemon=True).start()

    print(f"\n  {c('qanat', B)} {D}v{__version__}{X}  {project.name}")
    print(f"  mcp      {c(f'http://{args.host}:{args.port}/mcp', B)} "
          f"{D}scope {args.scope}{X}")
    print(f"  schedule {D}{len(sched._jobs)} job(s) on a clock, in this process{X}")
    print(f"  store    {D}{project.store_url(root)}{X}\n")
    sys.stdout.flush()  # uvicorn blocks next, and a piped stdout would never flush
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    finally:
        sched.stop()
        store.close()
    return 0


# ------------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qanat",
        description="A data platform for processing metrics from filings. It reads company filings, computes the metrics you define, and answers as of any past date. Delivered as an MCP server.",
    )
    p.add_argument("--version", action="version", version=f"qanat {__version__}")
    p.add_argument("-p", "--project", help="path to qanat.yaml (default: found by walking up)")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="scaffold a project")
    i.add_argument("--store",
                   help="where the tables live: a directory for parquet, or a .duckdb path")
    i.add_argument("--demo", action="store_true",
                   help="fetch the filings and parse them, so the project starts with rows in it")
    i.add_argument("directory", nargs="?", default=".")
    i.add_argument("--name")
    i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    ch = sub.add_parser("check", help="hold the pipeline against the stage contract")
    ch.set_defaults(func=cmd_check)

    mc = sub.add_parser("mcp", help="serve this project to an agent, over stdio or --http")
    mc.add_argument("--scope", choices=list(SCOPES), default=DEFAULT_SCOPE,
                    help="which tools to offer: data reads tables, research adds screens "
                         "and coverage, full adds ingest and metric definitions, authoring "
                         f"adds the pipeline itself (default: {DEFAULT_SCOPE})")
    mc.add_argument("--http", action="store_true",
                    help="serve MCP over HTTP instead of stdio, for a client that is not "
                         "on this machine")
    mc.add_argument("--schedule", action="store_true",
                    help="with --http: also run the clock in this process. Without it "
                         "nothing here reads a cron line")
    mc.add_argument("--host", default="127.0.0.1", help="with --http (default: 127.0.0.1)")
    mc.add_argument("--port", type=int, default=8421, help="with --http (default: 8421)")
    mc.add_argument("--token", help="with --http: the bearer token a caller must send. "
                                    "Required to bind anything but loopback. Also "
                                    "QANAT_MCP_TOKEN")
    mc.add_argument("--workers", type=int, default=4, help="with --schedule")
    mc.add_argument("--run-now", action="store_true",
                    help="with --schedule: fire every job once at startup")
    mc.add_argument("--log-level", default="warning")
    mc.add_argument("--force", action="store_true",
                    help="with --schedule: run even if the contract fails")
    # Removed, and kept only so it can say what replaced it rather than die in argparse.
    mc.add_argument("--read-only", action="store_true", help=argparse.SUPPRESS)
    mc.set_defaults(func=cmd_mcp)

    sc = sub.add_parser("schedule", help="run the clock, and serve nothing")
    sc.add_argument("--workers", type=int, default=4)
    sc.add_argument("--run-now", action="store_true", help="fire every job once at startup")
    sc.add_argument("--force", action="store_true", help="run even if the contract fails")
    sc.set_defaults(func=cmd_schedule)

    return p


def main(argv: list[str] | None = None) -> int:
    moved = _say_it_moved(list(argv if argv is not None else sys.argv[1:]))
    if moved is not None:
        return moved
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print()
        return 130
    except Exception as exc:  # noqa: BLE001
        print(c(f"{type(exc).__name__}: {exc}", R_), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
