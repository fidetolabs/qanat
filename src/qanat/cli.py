"""qanat -- the command line.

    qanat init [dir]     scaffold a project that runs green with no keys
    qanat check          hold the pipeline against the stage contract
    qanat ls             stages, tables, jobs
    qanat graph          the pipeline as a picture, in the terminal
    qanat run [job]      one pass, or one job
    qanat screen         which companies passed, on a date or over a window
    qanat mcp            serve the same tools to an agent, over stdio or --http, at one of three scopes
    qanat serve          run the scheduler, and serve this project over MCP
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from qanat import __version__
from qanat.mcp import SCOPES  # the three scope names, for --scope's choices

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


# ------------------------------------------------------------------------ init
def cmd_init(args) -> int:
    from qanat.scaffold import write_project

    target = Path(args.directory).resolve()
    name = args.name or target.name.replace("-", "_")
    target.mkdir(parents=True, exist_ok=True)
    written = write_project(target, name, force=args.force, postgres=args.postgres,
                            store=args.store)
    if not written:
        print(c("nothing written -- files already exist. Use --force to overwrite.", Y))
        return 1
    for p in written:
        print(f"  {c('+', G)} {p.relative_to(target)}")

    if not args.demo:
        where = "" if target == Path.cwd() else f"cd {args.directory} && "
        print(f"\n{where}qanat run && qanat serve")
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


# ------------------------------------------------------------------------ plan
def cmd_plan(args) -> int:
    from qanat.plan import plan
    from qanat.project import validate
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    for e in rep.errors:
        print(f"  {c('error', R_)} {e}")
    if not rep.ok:
        print(c("\nthe file is not legal yet, so there is nothing to compare.", R_))
        return 1

    store = Store(project.store_url(root))
    pl = plan(project, root, store)
    print(f"\n  {c('qanat plan', B)}  {D}{project.name}{X}\n")

    if pl.first_run:
        print(f"  {D}nothing has been applied yet, so everything below is new{X}\n")

    for ch in pl.adds:
        print(f"  {c('+', G)} {ch.target:<24} {D}{ch.note}{X}")
    for ch in pl.updates:
        print(f"  {c('~', Y)} {ch.target:<24} {D}{ch.details[0] if ch.details else ''}{X}")
        for line in ch.details[1:]:
            print(f"    {' ' * 25}{D}{line}{X}")
    for ch in pl.removes:
        print(f"  {c('-', R_)} {ch.target:<24} {D}{ch.note}{X}")
    for ch in pl.creates:
        print(f"  {c('+', G)} {ch.target:<24} {D}will be created, {ch.note}{X}")
    for ch in pl.orphans:
        print(f"  {c('-', R_)} {ch.target:<24} {D}orphan: {ch.note}{X}")

    if pl.empty:
        print(f"  {c('no changes', G)} {D}· the database matches the file{X}")
    else:
        parts = []
        for n, word in [(len(pl.adds), "to add"), (len(pl.updates), "changed"),
                        (len(pl.removes), "removed"), (len(pl.creates), "to create"),
                        (len(pl.orphans), "orphan")]:
            if n:
                parts.append(f"{n} {word}")
        print(f"\n  {', '.join(parts)}, {pl.unchanged} unchanged")
        if pl.orphans:
            print(f"  {D}qanat prune  removes the orphan table(s){X}")
    print()
    if args.exit_code and not pl.empty:
        return 2
    return 0


# ----------------------------------------------------------------------- prune
def cmd_prune(args) -> int:
    from qanat.plan import plan
    from qanat.store import Store

    project, root = _load(args)
    store = Store(project.store_url(root))
    pl = plan(project, root, store)
    if not pl.orphans and not pl.removes:
        print(c("nothing to prune", G))
        return 0

    print()
    for ch in [*pl.orphans, *pl.removes]:
        print(f"  {c('-', R_)} {ch.target:<24} {D}{ch.note}{X}")
    print()

    if not args.yes:
        if not sys.stdin.isatty():
            print(c("refusing to drop tables without --yes", R_))
            return 1
        n = len(pl.orphans)
        if input(f"  drop {n} table(s)? this cannot be undone [y/N] ").strip().lower() not in ("y", "yes"):
            print("  nothing dropped")
            return 0

    for ch in pl.orphans:
        rows = store.drop(ch.target)
        store.event("warn", "prune", f"dropped {ch.target} ({rows:,} rows)")
        print(f"  {c('dropped', Y)} {ch.target} {D}({rows:,} rows){X}")
    if pl.removes:
        store.forget_state([ch.target for ch in pl.removes])
        print(f"  {c('forgot', Y)}  {', '.join(ch.target for ch in pl.removes)} "
              f"{D}(no longer in the file){X}")
    store.close()
    print()
    return 0


# -------------------------------------------------------------------------- ls
def cmd_ls(args) -> int:
    from qanat.project import validate
    from qanat.store import Store

    project, root = _load(args)
    validate(project, root)
    store = Store(project.store_url(root))
    producers = project.producers()

    print(f"\n{c(project.name, B)}  {D}{root}{X}\n")
    for lay in project.stages:
        tables = [t for t in project.tables() if t.startswith(f"{lay.id}.")]
        print(f"  {c(lay.id, B)} {D}· {lay.kind}{X}  {D}{lay.description}{X}")
        for ref in tables:
            info = store.table_info(ref)
            rows = f"{info.rows:,} rows" if info else "not written yet"
            mark = c("●", G) if info else c("○", Y)
            print(f"    {mark} {ref.partition('.')[2]:<22} {rows:>16}  "
                  f"{D}from {producers.get(ref, '?')}{X}")
        if not tables:
            print(f"    {D}(empty){X}")
    print()
    for j in project.jobs:
        kind = "source" if hasattr(j, "connector") else "step"
        what = getattr(j, "connector", None) or getattr(j, "script", "")
        print(f"  {c(j.id, B):<28} {kind:<7} {D}{what:<24} {j.schedule or 'manual'}{X}")
    print()
    store.close()
    return 0


# ----------------------------------------------------------------------- graph
def cmd_graph(args) -> int:
    """The pipeline on a character grid.

    It reads `build_graph` rather than the file, so an arrow here is the arrow the
    `lineage` tool reports, and a row count is a count(*) either way.
    """
    import shutil

    from qanat.graph import build_graph, glyphs_for, ink_for, render
    from qanat.project import validate
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    if not rep.ok:
        # Still worth drawing: a picture of a project that does not hold is how
        # you see *where* it does not hold. But say so, and say it on stderr, so
        # the drawing itself still pipes clean.
        print(c(f"the contract does not hold ({len(rep.errors)} errors). "
                f"`qanat check` to see why -- drawing it anyway.", Y), file=sys.stderr)

    if args.width is None:
        width = shutil.get_terminal_size().columns if _TTY else 0
    else:
        width = args.width

    store = Store(project.store_url(root))
    try:
        graph = build_graph(store, project, root, None)
    finally:
        store.close()

    print(render(
        graph, root,
        ink=ink_for(args.color, sys.stdout),
        g=glyphs_for(sys.stdout, args.ascii),
        width=width or None,
        labels=not args.no_labels,
    ))
    return 0 if rep.ok else 1


# ------------------------------------------------------------------------- run
def cmd_run(args) -> int:
    from qanat.project import validate
    from qanat.runner import run_all, run_job
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    if not rep.ok and not args.force:
        print(c("the contract does not hold. `qanat check` to see why, or --force.", R_))
        return 1

    store = Store(project.store_url(root))
    _repair_after_crash(store, project, root)
    as_of, seed = getattr(args, "as_of", None), getattr(args, "seed", None)
    if as_of:
        store.open_pit(as_of, project.time_columns)
    try:
        results = (
            [run_job(store, project, root, args.job, as_of=as_of, seed=seed)]
            if args.job
            else run_all(store, project, root, as_of=as_of, seed=seed, sources=not as_of)
        )
    finally:
        if as_of:
            store.close_pit()

    print()
    for r in results:
        mark = c("ok ", G) if r.ok else c("fail", R_)
        detail = f"{r.rows:,} rows -> {', '.join(r.targets)}" if r.ok else (r.error or "")
        print(f"  {mark} {r.job_id:<22} {detail}")
    failed = [r for r in results if not r.ok]
    print()
    print(c(f"{len(results) - len(failed)}/{len(results)} ok", G if not failed else Y))
    store.close()
    return 1 if failed else 0


# ---------------------------------------------------------------------- screen
def cmd_screen(args) -> int:
    """Who passed, on a date or across a window."""
    import json as _json

    from qanat.screen import ScreenError, parse_filters, screen
    from qanat.store import Store

    project, root = _load(args)
    store = Store(project.store_url(root))
    try:
        raw = _json.loads(args.filter) if args.filter else []
        filters = parse_filters(raw)
        out = screen(store, project, filters, as_of=args.as_of,
                     frm=getattr(args, "frm", None), to=args.to, every=args.every)
    except ScreenError as exc:
        print(c(f"ScreenError: {exc}", R_))
        return 1
    finally:
        store.close()

    if args.json:
        print(_json.dumps(out, indent=2, default=str))
        return 0

    for count in out["counts"]:
        hits = [r for r in out["rows"] if r["as_of"] == count["as_of"] and r["passed"]]
        print(f"\n{c(str(count['as_of'])[:10], B)}  "
              f"{count['passed']} of {count['evaluated']} passed"
              + (f", {count['dropped']} could not be evaluated" if count["dropped"] else ""))
        for r in hits:
            fwd = r.get("forward_return")
            tail = f"   {fwd * 100:+.1f}% to {r.get('next_priced_on')}" if fwd is not None else ""
            print(f"  {c('·', D)} {r['subject']}{tail}")
    return 0


# ----------------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    import uvicorn

    from qanat.mcp_http import create_app
    from qanat.plan import plan as plan_project
    from qanat.project import validate
    from qanat.runtime import AppState
    from qanat.scheduler import Scheduler
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    for e in rep.errors:
        print(f"  {c('error', R_)} {e}")
    if not rep.ok and not args.force:
        print(c("refusing to serve a pipeline that breaks the contract. --force to override.", R_))
        return 1

    store = Store(project.store_url(root))
    _repair_after_crash(store, project, root)
    pl = plan_project(project, root, store)
    for ch in pl.changes:
        print(f"  {c('drift', Y)} {ch.target} · {ch.note or (ch.details[0] if ch.details else '')}")
    sched = None if args.no_schedule else Scheduler(store, project, root, workers=args.workers)

    state = AppState(store=store, project=project, root=root, sched=sched)
    # Binding to an address on purpose means meaning to be reached at it, so that
    # name is allowed. A wildcard bind names nothing, and never arrives in `Host`
    # anyway -- serving under a real hostname stays a deliberate setting.
    named = [] if args.host in ("0.0.0.0", "::", "") else [args.host]
    token = args.token or os.environ.get("QANAT_MCP_TOKEN") or None
    loopback = args.host in ("127.0.0.1", "::1", "localhost")
    if not loopback and not token:
        print(c(f"refusing to serve {args.host} with no token. Pass --token or set "
                f"QANAT_MCP_TOKEN.", R_))
        return 2
    app = create_app(None, args.scope, token=token, allow_hosts=named, state=state)
    if sched:
        sched.start()
        if args.run_now:
            # one full pass in dependency order -- firing every job at once would
            # only make the downstream steps fail on a table that is not there yet
            import threading

            from qanat.runner import run_all

            threading.Thread(
                target=run_all, args=(store, project, root), daemon=True
            ).start()

    print(f"\n  {c('qanat', B)} {D}v{__version__}{X}  {project.name}")
    print(f"  mcp      {c(f'http://{args.host}:{args.port}/mcp', B)} "
          f"{D}scope {args.scope}{X}")
    print(f"  store    {D}{project.store_url(root)}{X}\n")
    sys.stdout.flush()  # uvicorn blocks next, and a piped stdout would never flush
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    finally:
        if sched:
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
    if getattr(args, "read_only", False):
        print("qanat mcp: --read-only is gone. It split 33 tools into 23 and 10, which is "
              "too blunt to be a promise. Use --scope data to read tables, or --scope "
              "research to add screens. See docs/agents.md.", file=sys.stderr)
        return 2
    if args.http:
        from qanat.mcp_http import serve_http

        return serve_http(getattr(args, "project", None), scope=args.scope,
                          host=args.host, port=args.port, token=args.token)

    from qanat.mcp import serve_stdio

    return serve_stdio(getattr(args, "project", None), scope=args.scope)


# ------------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qanat",
        description="A data platform for investment research. It ingests financial data sources and computes point-in-time financial metrics for valuation and screening. Delivered as an MCP server.",
    )
    p.add_argument("--version", action="version", version=f"qanat {__version__}")
    p.add_argument("-p", "--project", help="path to qanat.yaml (default: found by walking up)")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="scaffold a project")
    i.add_argument("--store", help="where the tables live: a path, or a postgres:// url")
    i.add_argument("--demo", action="store_true",
                   help="fetch the filings and parse them, so the project starts with rows in it")
    i.add_argument("directory", nargs="?", default=".")
    i.add_argument("--name")
    i.add_argument("--force", action="store_true")
    i.add_argument(
        "--postgres",
        action="store_true",
        help="use postgresql://qanat:qanat@localhost:5432/qanat instead of DuckDB file",
    )
    i.set_defaults(func=cmd_init)

    ch = sub.add_parser("check", help="hold the pipeline against the stage contract")
    ch.set_defaults(func=cmd_check)

    pl = sub.add_parser("plan", help="what would change if this file were applied")
    pl.add_argument("--exit-code", action="store_true", help="exit 2 when there are changes")
    pl.set_defaults(func=cmd_plan)

    pr = sub.add_parser("prune", help="drop tables nothing produces any more")
    pr.add_argument("--yes", action="store_true", help="do not ask")
    pr.set_defaults(func=cmd_prune)

    ls = sub.add_parser("ls", help="stages, tables, jobs")
    ls.set_defaults(func=cmd_ls)

    gr = sub.add_parser("graph", help="the pipeline as a picture, in the terminal")
    gr.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="colour the stages (default: when stdout is a terminal)")
    gr.add_argument("--ascii", action="store_true", help="draw with - | + instead of box rules")
    gr.add_argument("--no-labels", action="store_true", dest="no_labels",
                    help="leave the step off each arrow, and draw narrower")
    gr.add_argument("--width", type=int,
                    help="fit to this many columns. 0 is unlimited (the default when piped)")
    gr.set_defaults(func=cmd_graph)


    r = sub.add_parser("run", help="one pass over the whole graph, or one job")
    r.add_argument("job", nargs="?")
    r.add_argument("--as-of", dest="as_of", help="screen one pass as of this timestamp")
    r.add_argument("--seed", type=int, help="pin every generator for this pass")
    r.add_argument("--force", action="store_true", help="run even if the contract fails")
    r.set_defaults(func=cmd_run)

    sc = sub.add_parser("screen", help="which companies passed, on a date or over a window")
    sc.add_argument("--filter", help='clauses as JSON: \'[{"metric":"pe","op":"<","value":20}]\'')
    sc.add_argument("--as-of", dest="as_of", help="one date")
    sc.add_argument("--from", dest="frm", help="start of a window")
    sc.add_argument("--to", help="end of a window")
    sc.add_argument("--every", default="1y", help="the gap between dates in a window")
    sc.add_argument("--json", action="store_true", help="the whole result, as JSON")
    sc.set_defaults(func=cmd_screen)


    mc = sub.add_parser("mcp", help="serve this project to an agent over MCP (stdio)")
    mc.add_argument("--scope", choices=list(SCOPES), default="full",
                    help="which tools to offer: data reads tables, research adds screens, "
                         "full adds authoring and ingest (default: full)")
    mc.add_argument("--http", action="store_true",
                    help="serve MCP over HTTP instead of stdio, for a client that is not "
                         "on this machine")
    mc.add_argument("--host", default="127.0.0.1", help="with --http (default: 127.0.0.1)")
    mc.add_argument("--port", type=int, default=8421, help="with --http (default: 8421)")
    mc.add_argument("--token", help="with --http: the bearer token a caller must send. "
                                    "Required to bind anything but loopback. Also "
                                    "QANAT_MCP_TOKEN")
    # Removed, and kept only so it can say what replaced it rather than die in argparse.
    mc.add_argument("--read-only", action="store_true", help=argparse.SUPPRESS)
    mc.set_defaults(func=cmd_mcp)

    s = sub.add_parser("serve", help="run the scheduler, and serve this project over MCP")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8421)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--no-schedule", action="store_true", help="serve MCP, run nothing")
    s.add_argument("--scope", choices=list(SCOPES), default="full",
                   help="which tools to offer over MCP (default: full)")
    s.add_argument("--token", help="the bearer token a caller must send. Required to bind "
                                   "anything but loopback. Also QANAT_MCP_TOKEN")
    s.add_argument("--run-now", action="store_true", help="fire every job once at startup")
    s.add_argument("--log-level", default="warning")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
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
