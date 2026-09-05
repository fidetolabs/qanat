"""qanat -- the command line.

    qanat init [dir]     scaffold a project that runs green with no keys
    qanat check          hold the pipeline against the stage contract
    qanat ls             stages, tables, jobs
    qanat run [job]      one pass, or one job
    qanat backtest       replay the graph over a window, and price what it held
    qanat report <id>    one backtest, period by period
    qanat mcp            serve the same tools to an agent over stdio
    qanat serve          scheduler + console
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qanat import __version__

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
    from qanat.scaffold import add_shelf_alphas, write_project

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

    added = add_shelf_alphas(target)
    for step_id in added:
        print(f"  {c('+', G)} {step_id}")
    print(f"\n{c('running the pipeline', D)}")
    if (rc := _demo_fill(target, added)) != 0:
        return rc
    where = "" if target == Path.cwd() else f"cd {args.directory} && "
    print(f"\n{where}qanat serve")
    return 0


def _demo_fill(target: Path, alphas: list[str]) -> int:
    """Run the pipeline, then price each alpha, so the console opens on a book
    with real numbers in it rather than a graph of empty boxes."""
    from qanat.backtest import BacktestError, dates, run_backtest
    from qanat.project import load
    from qanat.runner import run_all
    from qanat.store import Store

    project, root = load(target)
    store = Store(project.store_url(root))
    try:
        failed = [r for r in run_all(store, project, root) if not r.ok]
        if failed:
            for r in failed:
                print(f"  {c('error', R_)} {r.job_id}: {r.error}")
            return 1
        prices = store.read(project.backtest.prices)
        col = project.backtest.date_column
        frm, to = str(prices[col].min())[:10], str(prices[col].max())[:10]
        # leave the last stretch unscored, so `qanat serve` has somewhere to go
        stops = dates(frm, to, project.backtest.rebalance)
        if len(stops) > 4:
            to = stops[-3][:10]
        # The book's headline is the out-of-sample number, so a demo without a
        # split fills the console with sparklines and no figures -- and teaches
        # the habit of reading the whole-sample number, which is the one that
        # measures the choosing as much as the alpha.
        stops = dates(frm, to, project.backtest.rebalance)
        split = stops[len(stops) * 2 // 3][:10] if len(stops) > 5 else None
        def price(who, label):
            try:
                res = run_backtest(store, project, root, frm, to, alpha=who, seed=7,
                                   split=split)
                net = (res.totals or {}).get("net", 0.0)
                oos = ((res.segments or {}).get("out_of_sample") or {}).get("net")
                tone = G if net > 0 else R_
                tail = "" if oos is None else \
                    f"   {D}out of sample{X} {c(f'{oos * 100:+.2f}%', G if oos > 0 else R_)}"
                print(f"  {c('·', D)} {label:<26} net {c(f'{net * 100:+.2f}%', tone)}{tail}")
            except BacktestError as exc:
                print(f"  {c('warn', Y)} {label}: {exc}")

        for step_id in alphas:
            price(step_id, step_id)
        # and two of them priced as one book, which is a different strategy from
        # either alone and the thing a single-alpha demo never shows
        blend = [a for a in ("alpha_momentum", "alpha_low_vol") if a in alphas]
        if len(blend) == 2:
            price(blend, " + ".join(b.removeprefix("alpha_") for b in blend))
        return 0
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


# -------------------------------------------------------------------- backtest
def _money(x: float | None) -> str:
    return "     —" if x is None else f"{x * 100:+7.3f}%"


def _allocation(text: str | None) -> dict[str, float] | None:
    """`momentum=3,low_vol=1` -> {"momentum": 3.0, "low_vol": 1.0}. Shares are
    ratios, so nobody has to do the division by hand."""
    if not text:
        return None
    out: dict[str, float] = {}
    for part in text.split(","):
        name, _, share = part.partition("=")
        if not name.strip() or not share.strip():
            raise SystemExit(f"--allocation wants name=share pairs, got {part.strip()!r}")
        try:
            out[name.strip()] = float(share)
        except ValueError:
            raise SystemExit(f"--allocation share must be a number, got {share.strip()!r}") from None
    return out


def cmd_backtest(args) -> int:
    import json

    from qanat.backtest import run_backtest
    from qanat.project import validate
    from qanat.store import Store

    project, root = _load(args)
    rep = validate(project, root)
    for e in rep.errors:
        print(f"  {c('error', R_)} {e}")
    if not rep.ok and not args.force:
        print(c("the contract does not hold. `qanat check` to see why, or --force.", R_))
        return 1

    store = Store(project.store_url(root))
    try:
        print(f"\n  {c('replay', B)}  {project.name}  {D}{args.frm} → {args.to}"
              f" every {args.rebalance or (project.backtest.rebalance if project.backtest else '?')}"
              f", seed {args.seed}{X}\n")
        res = run_backtest(
            store, project, root, args.frm, args.to,
            rebalance=args.rebalance, seed=args.seed,
            decay=args.decay, universe=args.universe, split=args.split, alpha=args.alpha,
            allocation=_allocation(args.allocation),
            fee_bps=args.fee_bps, slippage_bps=args.slippage_bps,
            purge=args.purge, embargo=args.embargo,
            on_step=None if args.quiet else
            (lambda stop, n: print(f"  {c('·', D)} {stop[:19]}  {n:>3} holdings")),
        )
    finally:
        store.close()

    if args.json:
        print(json.dumps(res.as_dict(), indent=2, default=str))
        return 0 if res.periods and not res.failures else 1

    for f in res.failures:
        print(f"  {c('fail', R_)} {f}")
    for n in res.notes[:8]:
        print(f"  {c('note', Y)} {n}")

    t = res.totals
    if not t.get("periods"):
        print(c("\n  no period could be priced, so there is nothing to report\n", R_))
        return 1

    print(f"\n  {c('net edge', B)}  {D}run {res.run_id} · digest {res.digest}{X}\n")
    print(f"    gross            {_money(t['gross'])}")
    print(f"    fees             {_money(-t['fees'])}")
    print(f"    slippage         {_money(-t['slippage'])}")
    print(f"    {'-' * 30}")
    # The three lines above are per-period amounts and add up. The headline is what
    # the money did, which compounds -- so the two differ, and saying so is cheaper
    # than letting somebody discover it with a calculator.
    net, added = t["net"], t.get("net_sum", t["net"])
    print(f"    {'sum of periods':<16} {_money(added)}")
    print(f"    {c('net', G if net > 0 else R_)}              "
          f"{c(_money(net), G if net > 0 else R_)}  {D}compounded{X}")
    print()
    seg = res.segments or {}
    if seg.get("in_sample") and seg.get("out_of_sample"):
        a, b = seg["in_sample"], seg["out_of_sample"]
        print(f"    {'in sample':<16} {_money(a.get('net'))}  {D}{a.get('periods', 0)} periods, "
              f"{_money(a.get('net_per_period'))} each{X}")
        print(f"    {'out of sample':<16} {_money(b.get('net'))}  {D}{b.get('periods', 0)} periods, "
              f"{_money(b.get('net_per_period'))} each{X}")
        print(f"    {D}split at {seg['split'][:10]}{X}")
    if seg.get("live") and seg["live"].get("periods"):
        lv = seg["live"]
        print(f"    {c('live', G)}             {_money(lv.get('net'))}  {D}{lv['periods']} periods, "
              f"{_money(lv.get('net_per_period'))} each{X}")
        print(f"    {D}after {seg['frontier'][:10]} · these rows did not exist when you chose{X}")
    if seg.get("in_sample") or seg.get("live"):
        print()
    if seg.get("warning"):
        print(f"    {c('warn', Y)} {seg['warning']}")

    print(f"    per period       {_money(t['net_per_period'])}  {D}over {t['periods']} periods{X}")
    print(f"    hit rate         {t['hit_rate'] * 100:6.1f}%")
    print(f"    turnover         {t['turnover']:7.2f}  {D}sum of |weight changes|{X}")
    print(f"    best / worst     {_money(t['best_period'])} / {_money(t['worst_period'])}")
    print(f"\n  {D}qanat report {res.run_id}   the periods, one by one{X}\n")
    return 0 if not res.failures else 1


def cmd_backtests(args) -> int:
    from qanat.store import Store

    project, root = _load(args)
    store = Store(project.store_url(root))
    rows = store.backtests(args.limit)
    store.close()
    if not rows:
        print(c("no backtests yet. Try `qanat backtest --from ... --to ...`", Y))
        return 0
    print(f"\n  {c('run', B):<22} {'window':<26} {'every':<7} {'seed':>5} "
          f"{'periods':>8} {'net':>9}  status")
    for r in rows:
        net = _money(r["net"]) if r["net"] is not None else "     —"
        window = f"{str(r['from_date'])[:10]} → {str(r['to_date'])[:10]}"
        print(f"  {r['run_id']:<22} {window:<26} {r['rebalance']:<7} {r['seed']:>5} "
              f"{r['periods'] or 0:>8} {net:>9}  {r['status']}")
    print()
    return 0


def cmd_report(args) -> int:
    import json

    from qanat.store import Store

    project, root = _load(args)
    store = Store(project.store_url(root))
    row = store.backtest(int(args.run_id))
    store.close()
    if row is None:
        print(c(f"no backtest {args.run_id}", R_))
        return 1
    if args.json:
        print(row["report"] or json.dumps(row, default=str))
        return 0
    if row.get("error"):
        print(f"  {c('failed', R_)} {row['error']}")
        return 1
    report = json.loads(row["report"]) if row.get("report") else {}
    t = report.get("totals", {})
    print(f"\n  {c('run ' + str(row['run_id']), B)}  {D}{row['from_date']} → {row['to_date']}"
          f" every {row['rebalance']} · seed {row['seed']} · digest {row['digest']}{X}\n")
    print(f"  {'as of':<21} {'priced':<23} {'held':>5} {'gross':>9} {'cost':>9} {'net':>9}")
    for p_ in report.get("periods", []):
        cost = p_["fees"] + p_["slippage"]
        print(f"  {p_['as_of'][:19]:<21} {p_['priced_from'][:10]} → {p_['priced_to'][:10]}   "
              f"{p_['holdings']:>5} {_money(p_['gross']):>9} {_money(-cost):>9} "
              f"{c(_money(p_['net']), G if p_['net'] > 0 else R_):>9}")
    if t:
        print(f"\n  {c('net', B)} {_money(t['net'])}  {D}over {t['periods']} periods, "
              f"hit rate {t['hit_rate'] * 100:.0f}%{X}")
    for n in report.get("notes", [])[:10]:
        print(f"  {c('note', Y)} {n}")
    print()
    return 0


def cmd_compare(args) -> int:
    from qanat.backtest import compare
    from qanat.store import Store

    project, root = _load(args)
    store = Store(project.store_url(root))
    a, b = store.backtest(int(args.a)), store.backtest(int(args.b))
    store.close()
    for rid, row in ((args.a, a), (args.b, b)):
        if row is None:
            print(c(f"no backtest {rid}", R_))
            return 1
    out = compare(a, b)
    print(f"\n  {c('compare', B)}  {out['a']}  →  {out['b']}")
    print(f"  {D}{out['note']}{X}\n")
    for k, m in out["moved"].items():
        if m["delta"] is None:
            print(f"    {k:<12} {'—':>12}")
        elif k in ("periods", "turnover"):
            print(f"    {k:<12} {m['a']:>10.3f} → {m['b']:>10.3f}   {m['delta']:+10.3f}")
        else:
            print(f"    {k:<12} {_money(m['a']):>10} → {_money(m['b']):>10}   {_money(m['delta']):>10}")
    print()
    return 0


# ----------------------------------------------------------------------- serve
def cmd_serve(args) -> int:
    import uvicorn

    from qanat.api import AppState, create_app
    from qanat.plan import plan as plan_project
    from qanat.project import validate
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
    pl = plan_project(project, root, store)
    for ch in pl.changes:
        print(f"  {c('drift', Y)} {ch.target} · {ch.note or (ch.details[0] if ch.details else '')}")
    sched = None if args.no_schedule else Scheduler(store, project, root, workers=args.workers)

    state = AppState(store=store, project=project, root=root, sched=sched)
    app = create_app(state)
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
    print(f"  console  {c(f'http://{args.host}:{args.port}', B)}")
    print(f"  api      {D}http://{args.host}:{args.port}/api/docs{X}")
    print(f"  store    {D}{project.store_url(root)}{X}\n")
    sys.stdout.flush()  # uvicorn blocks next, and a piped stdout would never flush
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    finally:
        if sched:
            sched.stop()
        store.close()
    return 0


# ---------------------------------------------------------------------- alphas
def cmd_alphas(args) -> int:
    from qanat import alphas

    if not args.name:
        print(f"\n  {c('alphas on the shelf', B)}\n")
        for a in alphas.describe():
            print(f"  {c(a['name'], B):<28} {D}{a['side']}{X}")
            print(f"    {a['title']}")
            print(f"    {D}{a['why']}{X}")
            print(f"    {D}options: {a['options']}{X}\n")
        print(f"  {D}qanat alphas <name> --reads <stage.table>   wire one up{X}\n")
        return 0

    from qanat.editor import save_step

    project, root = _load(args)
    if args.name not in alphas.CATALOGUE:
        print(c(f"no alpha called '{args.name}'. On the shelf: "
                f"{', '.join(alphas.CATALOGUE)}", R_))
        return 1
    if not args.reads:
        print(c("--reads is required: the table holding symbol, date and price", R_))
        print(f"  {D}tables here: {', '.join(project.tables())}{X}")
        return 1

    wl = project.weights_stage
    step_id = f"alpha_{args.name}"
    target = f"{wl.id}.{args.name}"
    universe = args.universe or next((st.universe for st in project.steps if st.universe), None)
    if not universe:
        print(c("this project declares no universe, and every shelf alpha needs one", R_))
        return 1

    script = alphas.write_alpha(root, args.name)
    opts = dict(alphas.CATALOGUE[args.name]["options"])
    opts["reads"] = args.reads
    save_step(project, root, {"id": step_id, "from": [args.reads], "to": [target],
                              "script": script, "universe": universe, "options": opts},
              create_script=False)
    print(f"  {c('+', G)} {step_id:<24} {D}{args.reads} → {target}, universe {universe}{X}")
    print(f"  {c('+', G)} {script}")
    print(f"\n  {D}qanat run && qanat backtest --alpha {step_id} --from … --to …{X}\n")
    return 0


# ------------------------------------------------------------------------- mcp
def cmd_mcp(args) -> int:
    from qanat.mcp import serve_stdio

    return serve_stdio(getattr(args, "project", None), read_only=args.read_only)


# ------------------------------------------------------------------------ main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qanat",
        description="Agent-native workflow engine for building and backtesting alphas as DAGs.",
    )
    p.add_argument("--version", action="version", version=f"qanat {__version__}")
    p.add_argument("-p", "--project", help="path to qanat.yaml (default: found by walking up)")
    sub = p.add_subparsers(dest="command", required=True)

    i = sub.add_parser("init", help="scaffold a project")
    i.add_argument("--store", help="where the tables live: a path, or a postgres:// url")
    i.add_argument("--demo", action="store_true",
                   help="wire the four shelf alphas, run the pipeline and price each one")
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

    r = sub.add_parser("run", help="one pass over the whole graph, or one job")
    r.add_argument("job", nargs="?")
    r.add_argument("--as-of", dest="as_of", help="replay one pass as of this timestamp")
    r.add_argument("--seed", type=int, help="pin every generator for this pass")
    r.add_argument("--force", action="store_true", help="run even if the contract fails")
    r.set_defaults(func=cmd_run)

    bt = sub.add_parser("backtest", help="replay the pipeline and price what it held")
    bt.add_argument("--from", dest="frm", required=True, help="first as-of date, e.g. 2024-01-01")
    bt.add_argument("--to", required=True, help="last as-of date")
    bt.add_argument("--rebalance", help="gap between as-of dates (default: from qanat.yaml)")
    bt.add_argument("--seed", type=int, default=0, help="pins every generator, so a rerun repeats")
    bt.add_argument("--decay", type=int,
                    help="hold a blend of the last N portfolios, newest heaviest. Cuts turnover")
    bt.add_argument("--universe", help="run the alpha against this universe instead of the file's")
    bt.add_argument("--split", help="first out-of-sample date. Before it is in-sample")
    bt.add_argument("--alpha", help="which alpha to price (needed once there is more than one). "
                                    "Name several, comma separated, to price them as one book")
    bt.add_argument("--allocation", help="share per alpha in a blend, e.g. momentum=3,low_vol=1. "
                                         "Equal split if left out")
    bt.add_argument("--fee-bps", type=float, dest="fee_bps",
                    help="commission on turnover, in basis points (default: from qanat.yaml)")
    bt.add_argument("--slippage-bps", type=float, dest="slippage_bps",
                    help="slippage on turnover, in basis points")
    bt.add_argument("--purge", help="hold rows back this long before a step may read them, e.g. 1d")
    bt.add_argument("--embargo", help="wait this long after the as-of date before a return counts")
    bt.add_argument("--json", action="store_true")
    bt.add_argument("--quiet", action="store_true", help="do not print each as-of date")
    bt.add_argument("--force", action="store_true", help="run even if the contract fails")
    bt.set_defaults(func=cmd_backtest)

    bl = sub.add_parser("backtests", help="every replay this project has run")
    bl.add_argument("--limit", type=int, default=25)
    bl.set_defaults(func=cmd_backtests)

    rp = sub.add_parser("report", help="one backtest, period by period")
    rp.add_argument("run_id")
    rp.add_argument("--json", action="store_true")
    rp.set_defaults(func=cmd_report)

    cp = sub.add_parser("compare", help="what moved between two backtests")
    cp.add_argument("a")
    cp.add_argument("b")
    cp.set_defaults(func=cmd_compare)

    al = sub.add_parser("alphas", help="the alphas that ship with qanat, and how to wire one")
    al.add_argument("name", nargs="?", help="wire this one up as the weights step")
    al.add_argument("--reads", help="stage.table holding symbol, date and price")
    al.add_argument("--universe", help="which universe it may hold")
    al.set_defaults(func=cmd_alphas)

    mc = sub.add_parser("mcp", help="serve this project to an agent over MCP (stdio)")
    mc.add_argument("--read-only", action="store_true", help="expose no tool that writes")
    mc.set_defaults(func=cmd_mcp)

    s = sub.add_parser("serve", help="scheduler and console")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8420)
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--no-schedule", action="store_true", help="serve the console, run nothing")
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
