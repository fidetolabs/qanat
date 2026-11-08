"""qanat -- the command line. Five verbs, and each one is here for a reason.

    qanat init [dir]     scaffold a project that runs green with no keys
    qanat check          hold the pipeline against the stage contract
    qanat mcp            serve this project to an agent, over stdio
    qanat serve          the HTTP server: --mcp, --app, --schedule
    qanat schedule       run the clock, and serve nothing

**This is reached by an agent, so most of what was here was the wrong door.**
`ls`, `graph`, `plan`, `run`, `screen` and `prune` were a person's way into a
thing whose caller is a program, and each one was a second implementation of a
question a tool already answers -- a second place the behaviour could quietly
differ. They are gone, each named below beside what replaced it:

    ls        list_tables, list_pipeline, describe_table -- it was those three in one dump
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

**There is no `migrate`.** A 0.10.x project is refused at load with the keys it
got wrong named, and that is the whole of it: a converter is a second reader of
a vocabulary this version does not have, and it would have to be kept correct
against every later change to earn its place.

**And the clock and the server are two commands, not one with a flag.** They
used to be one process -- scheduler and MCP server together, with
`--no-schedule` to turn the clock off and no way at all to run the clock alone.
That is backwards for the two deployments that exist: a recorder wants a clock
and no server, and a hosted reader wants a server over a store somebody else
fills. `qanat schedule` is the clock, and it serves nothing.

**`serve` is back, and it is not what it was.** It went in 0.10.0 because it
forced the clock on; splitting the clock out fixed that, and the clock is an
optional flag here. What brought it back is that `qanat mcp --http` had stopped
describing its own program. **Over stdio this process is the client's child --
no port, no HTTP, and a clock cannot live in it. With `--http` it was an HTTP
server that happened to carry MCP on one endpoint.** Those are two programs, not
two settings, and `--http` was the tell: **a flag that changes what the program
is belongs in the verb.**

It was wrong a second way once the pages arrived. An HTTP server carrying only
pages was still called `mcp`, and `/mcp` was on the port whether the person
wanted it or not -- there was no flag to take it off.

    qanat mcp                      stdio. The agent's child process
    qanat serve --mcp              /mcp only
    qanat serve --app              the pages only, nothing at /mcp
    qanat serve --mcp --app        both, one program
    qanat serve --app --schedule   the pages and the clock
    qanat schedule                 the clock alone, no port

**The verb names the program, and a flag only ever adds a door.** Never
subtracts: there is no `--no-mcp` and no `qanat app`, so each arrangement has
exactly one spelling and no two spellings can disagree. **`qanat serve` with no
door is refused** -- serving nothing is not a default.

**Doors are free and programs are not.** The store takes one writer, so two
`qanat serve` on one project is the thing that breaks; two doors in one process
share a store and a lock, and cannot.
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
    "ls": "the list_tables, list_pipeline and describe_table tools. `ls` was those "
          "three in one dump",
    "graph": "nothing. It drew the pipeline for a person to look at, and this is "
             "reached by an agent",
    "plan": "the `plan` tool",
    "prune": "the `plan` tool's `prune` flag, so you see the orphans from the same "
             "call that drops them",
    "run": "the `run` tool",
    "screen": "the `screen` tool",
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
                print(f"  {c('·', D)} {r.definition_id:<26} {c(f'{r.rows:,} rows', G)}")
            else:
                failed.append(r)
        for r in failed:
            print(f"  {c('error', R_)} {r.definition_id}: {r.error}")
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
                f"{len(project.definitions)} definitions, "
                f"{len(project.job_schedules)} schedules · contract holds", G))
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
    print(f"  schedule {c(f'{len(sched._schedules)} schedule(s) on a clock', B)} "
          f"{D}· nothing is served from this process{X}")
    print(f"  store    {D}{project.store_url(root)}{X}\n")
    sys.stdout.flush()

    sched.start()
    if args.run_now:
        # One full pass in dependency order. Firing every job at once would only
        # make the downstream transforms fail on a table that is not there yet.
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
    failed = [r for r in run_all(store, project, root, ingest=False) if not r.ok]
    for r in failed:
        print(f"  {c('error', R_)} {r.definition_id}: {r.error}")
    if not failed:
        print(f"  {c('ok', G)} rebuilt\n")


# ------------------------------------------------------------------------- mcp
def cmd_mcp(args) -> int:
    """Serve this project to an agent, over stdio. Nothing else.

    **This process is the client's child.** It reads stdin and writes stdout,
    there is no port, and a clock started here would die with the conversation --
    which `list_subscriptions` reports rather than hides. `qanat serve` is the
    HTTP server, and it is a different program rather than this one with a flag.
    """
    if getattr(args, "read_only", False):
        print("qanat mcp: --read-only is gone. It split 33 tools into 23 and 10, which is "
              "too blunt to be a promise. Use --scope data to read tables, or --scope "
              "research to add screens. See docs/agents.md.", file=sys.stderr)
        return 2

    # Both are kept as suppressed flags rather than dropped, so they can say
    # where they went instead of dying in argparse as an unrecognised argument.
    if getattr(args, "http", False):
        print("qanat mcp: --http is gone. `qanat mcp` is the stdio server -- your "
              "client's child, with no port. The HTTP server is its own verb now: "
              "`qanat serve --mcp`, or `qanat serve --mcp --app` to add the pages.",
              file=sys.stderr)
        return 2

    if getattr(args, "schedule", False):
        print("qanat mcp: --schedule is gone from here. Over stdio this server is your "
              "client's child, so a clock started here dies with the conversation. Run "
              "`qanat schedule` beside it, or `qanat serve --mcp --schedule` for one "
              "process doing both.", file=sys.stderr)
        return 2

    from qanat.mcp import serve_stdio

    return serve_stdio(getattr(args, "project", None), scope=args.scope)


# ----------------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    """The HTTP server, carrying the doors the flags name.

    **The verb is the program and the flags are its doors.** `--mcp` is the tool
    endpoint for an agent or for hosted Margiana; `--app` is the pages and `/api`
    for the person running this Qanat; `--schedule` puts the clock in this
    process too. Neither door calls the other, and both reach the store through
    one lock, so **two doors here are not two writers** -- two of *these
    processes* on one project would be.
    """
    doors = tuple(d for d, on in (("mcp", args.mcp), ("app", args.app)) if on)
    if not doors:
        print("qanat serve: name at least one door. `--mcp` for the tool endpoint, "
              "`--app` for the pages, or both for one process serving both. Serving "
              "nothing is not a default.", file=sys.stderr)
        return 2

    if not args.schedule:
        from qanat.mcp_http import serve_http

        return serve_http(getattr(args, "project", None), scope=args.scope,
                          host=args.host, port=args.port, token=args.token,
                          doors=doors)

    return _serve_with_clock(args, doors)


def _serve_with_clock(args, doors: tuple[str, ...]) -> int:
    """The same server with a scheduler in the same process.

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
    app = create_app(None, args.scope, token=token, allow_hosts=named, state=state,
                     doors=doors)
    sched.start()
    if args.run_now:
        import threading

        from qanat.runner import run_all

        threading.Thread(target=run_all, args=(store, project, root), daemon=True).start()

    print(f"\n  {c('qanat', B)} {D}v{__version__}{X}  {project.name}")
    if "mcp" in doors:
        print(f"  mcp      {c(f'http://{args.host}:{args.port}/mcp', B)} "
              f"{D}scope {args.scope}{X}")
    if "app" in doors:
        print(f"  app      {c(f'http://{args.host}:{args.port}/', B)}")
    print(f"  schedule {D}{len(sched._schedules)} schedule(s) on a clock, in this process{X}")
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

    _SCOPE_HELP = ("which tools to offer: data reads tables, research adds screens "
                   "and coverage, full adds ingest and metric definitions, authoring "
                   f"adds the pipeline itself (default: {DEFAULT_SCOPE})")

    mc = sub.add_parser("mcp", help="serve this project to an agent, over stdio")
    mc.add_argument("--scope", choices=list(SCOPES), default=DEFAULT_SCOPE,
                    help=_SCOPE_HELP)
    # Three that went, kept only so they can say where rather than die in argparse
    # as an unrecognised argument.
    mc.add_argument("--http", action="store_true", help=argparse.SUPPRESS)
    mc.add_argument("--schedule", action="store_true", help=argparse.SUPPRESS)
    mc.add_argument("--read-only", action="store_true", help=argparse.SUPPRESS)
    mc.set_defaults(func=cmd_mcp)

    sv = sub.add_parser("serve", help="the HTTP server: --mcp, --app, --schedule")
    sv.add_argument("--mcp", action="store_true",
                    help="serve the tool endpoint at /mcp, for an agent or a hosted "
                         "client that is not on this machine")
    sv.add_argument("--app", action="store_true",
                    help="serve the pages and their API at /, for the person running "
                         "this Qanat. Writes follow --scope like the tools")
    sv.add_argument("--schedule", action="store_true",
                    help="also run the clock in this process. Without it nothing here "
                         "reads a cron line")
    sv.add_argument("--scope", choices=list(SCOPES), default=DEFAULT_SCOPE,
                    help=_SCOPE_HELP)
    sv.add_argument("--host", default="127.0.0.1", help="(default: 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8421, help="(default: 8421)")
    sv.add_argument("--token", help="the bearer token a caller must send. Required to "
                                    "bind anything but loopback. Also QANAT_MCP_TOKEN")
    sv.add_argument("--workers", type=int, default=4, help="with --schedule")
    sv.add_argument("--run-now", action="store_true",
                    help="with --schedule: fire every definition once at startup")
    sv.add_argument("--log-level", default="warning")
    sv.add_argument("--force", action="store_true",
                    help="with --schedule: run even if the contract fails")
    sv.set_defaults(func=cmd_serve)

    sc = sub.add_parser("schedule", help="run the clock, and serve nothing")
    sc.add_argument("--workers", type=int, default=4)
    sc.add_argument("--run-now", action="store_true", help="fire every definition once at startup")
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
