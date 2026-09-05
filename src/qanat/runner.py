"""Running one job -- a source poll, or a step.

Every run writes a row into `_qanat_runs` before it starts and after it ends, so
the console is reading history rather than being told a story.
"""

from __future__ import annotations

import importlib.util
import json
import random
import re
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from qanat import sources as source_adapters
from qanat.context import Context
from qanat.models import Project, Source, Step
from qanat.store import Store, phys

_VAR = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


def _record_applied(store: Store, root: Path, job: Source | Step) -> None:
    """What this job looked like the last time it worked -- the basis `qanat plan`
    compares against. Written only on success, so a failed run never becomes the
    thing a later change is measured from."""
    from qanat.plan import digest, job_spec

    spec = job_spec(job, root)
    store.set_state(job.id, digest(spec), json.dumps(spec, sort_keys=True))


@dataclass
class RunResult:
    job_id: str
    status: str
    rows: int = 0
    error: str | None = None
    targets: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# --------------------------------------------------------------------- sources
def run_source(store: Store, project: Project, root: Path, src: Source) -> RunResult:
    run_id = store.start_run(src.id, "source", [src.ref])
    try:
        df = source_adapters.fetch(src, root)
        if df is None or df.empty:
            _record_applied(store, root, src)
            store.event("warn", src.id, "source returned no rows")
            store.end_run(run_id, "ok", 0)
            return RunResult(src.id, "ok", 0, targets=(src.ref,))
        rows = store.write(src.ref, df, mode=src.mode, key=src.key)
        _record_applied(store, root, src)
        store.event("ok", src.id, f"landed {rows:,} rows in {src.ref}")
        store.end_run(run_id, "ok", rows)
        return RunResult(src.id, "ok", rows, targets=(src.ref,))
    except Exception as exc:  # noqa: BLE001 -- a failing source must not stop the loop
        msg = f"{type(exc).__name__}: {exc}"
        store.event("error", src.id, msg)
        store.end_run(run_id, "failed", 0, msg)
        return RunResult(src.id, "failed", 0, msg, (src.ref,))


# ----------------------------------------------------------------------- steps
def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"qanat_step_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import step script {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(seed: int | None) -> None:
    """Pin every generator a step might reach for, so a replay repeats exactly."""
    if seed is None:
        return
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except ImportError:  # pragma: no cover -- numpy is a dependency, but do not require it here
        pass


def _run_sql(store: Store, ctx: Context, step: Step, script: Path) -> int:
    sql = script.read_text()
    names: dict[str, Any] = {**step.options}
    if ctx.as_of:
        names["as_of"] = ctx.as_of
    sql = _VAR.sub(lambda m: str(names.get(m.group(1), m.group(0))), sql)
    if step.universe:
        store.con.register("universe", ctx.universe())
    try:
        return store.sql_into(step.writes[0], sql.strip().rstrip(";"))
    finally:
        if step.universe:
            store.con.unregister("universe")


def _run_python(store: Store, ctx: Context, step: Step, script: Path) -> int:
    mod = _load_module(script)
    if not hasattr(mod, "run"):
        raise AttributeError(f"{script.name} must define run(ctx)")
    out = mod.run(ctx)

    if isinstance(out, pd.DataFrame):
        if len(step.writes) != 1:
            raise ValueError(
                f"step '{step.id}' writes {len(step.writes)} tables, so run(ctx) must return "
                "a dict of {table: dataframe}"
            )
        out = {step.writes[0]: out}
    if not isinstance(out, dict):
        raise TypeError(f"step '{step.id}': run(ctx) must return a DataFrame or a dict of them")

    total = 0
    declared = {w.partition(".")[2]: w for w in step.writes}
    for key, df in out.items():
        ref = key if key in step.writes else declared.get(key)
        if ref is None:
            raise KeyError(
                f"step '{step.id}' returned '{key}', which it did not declare in writes "
                f"({', '.join(step.writes)})"
            )
        total += store.write(ref, df)
    missing = set(step.writes) - {k if k in step.writes else declared.get(k) for k in out}
    if missing:
        raise ValueError(f"step '{step.id}' declared but did not write: {', '.join(sorted(missing))}")
    return total


def _check_weights(store: Store, project: Project, ctx: Context, step: Step) -> None:
    """The one table in the weights stage is a portfolio, so say so if it is not."""
    wl = project.weights_stage
    if wl is None:
        return
    for ref in step.writes:
        if not ref.startswith(f"{wl.id}."):
            continue
        df = store.read(ref)
        cols = {c.lower() for c in df.columns}
        if not {"symbol", "weight"} <= cols:
            store.event("warn", step.id, f"{ref} has no symbol/weight columns -- is it a portfolio?")
            continue
        total = float(df["weight"].abs().sum())
        if abs(total - 1.0) > 0.01:
            store.event("warn", step.id, f"{ref}: |weights| sum to {total:.4f}, not 1.0")
        if step.universe:
            allowed = set(ctx.universe()["symbol"])
            stray = sorted(set(df["symbol"]) - allowed)
            if stray:
                store.event(
                    "warn", step.id,
                    f"{ref}: {len(stray)} symbol(s) outside universe '{step.universe}': "
                    + ", ".join(stray[:5]),
                )


def _check_lookahead(store: Store, project: Project, step: Step, as_of: str) -> None:
    """A replayed step may not write a row stamped after the moment it ran.

    The as-of views already keep the future out of what a step reads. This catches
    the other way in -- a step that built a timestamp itself, or read around the
    views with raw SQL -- and it fails the step rather than warning, because a
    number computed from the future is worse than no number.
    """
    import pandas as pd

    cut = pd.Timestamp(as_of)
    for ref in step.writes:
        col = store.time_column(ref, project.time_columns.get(ref))
        if col is None:
            continue
        newest = store.max_time(ref, project.time_columns.get(ref))
        if newest is None:
            continue
        if pd.Timestamp(newest) > cut:
            raise ValueError(
                f"lookahead: '{step.id}' wrote {ref} with {col} = {newest}, "
                f"which is after the as-of date {as_of}"
            )


def run_step(
    store: Store,
    project: Project,
    root: Path,
    step: Step,
    as_of: str | None = None,
    seed: int | None = None,
    universe: str | None = None,
) -> RunResult:
    run_id = store.start_run(step.id, "step", list(step.writes))
    ctx = Context(store, project, root, step, as_of=as_of, universe=universe)
    script = root / step.script
    try:
        missing = [r for r in step.reads if not store.exists(r)]
        if missing:
            raise LookupError(f"upstream table(s) not there yet: {', '.join(missing)}")

        _seed(seed)
        rows = (
            _run_sql(store, ctx, step, script)
            if script.suffix == ".sql"
            else _run_python(store, ctx, step, script)
        )
        if as_of:
            _check_lookahead(store, project, step, as_of)
        _check_weights(store, project, ctx, step)
        _record_applied(store, root, step)
        store.event("ok", step.id, f"wrote {rows:,} rows to {', '.join(step.writes)}")
        store.end_run(run_id, "ok", rows)
        return RunResult(step.id, "ok", rows, targets=tuple(step.writes))
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        store.event("error", step.id, msg.splitlines()[0][:400])
        store.end_run(run_id, "failed", 0, traceback.format_exc(limit=3)[:2000])
        return RunResult(step.id, "failed", 0, msg, tuple(step.writes))


# ------------------------------------------------------------------- the graph
def order(project: Project) -> list[Step]:
    """Steps, sorted so that a step runs after everything it reads."""
    remaining = list(project.steps)
    produced = {s.ref for s in project.sources}
    out: list[Step] = []
    while remaining:
        ready = [s for s in remaining if all(r in produced for r in s.reads)]
        if not ready:
            out.extend(remaining)  # a cycle; `qanat check` is what reports it
            break
        for s in ready:
            out.append(s)
            produced.update(s.writes)
            remaining.remove(s)
    return out


def run_job(
    store: Store,
    project: Project,
    root: Path,
    job_id: str,
    as_of: str | None = None,
    seed: int | None = None,
    universe: str | None = None,
) -> RunResult:
    job = project.job(job_id)
    if job is None:
        raise KeyError(f"no source or step called '{job_id}'")
    if isinstance(job, Source):
        if as_of:
            raise ValueError(
                f"'{job_id}' is a source. A replay reads what already landed; "
                "it never polls the outside world again"
            )
        return run_source(store, project, root, job)
    return run_step(store, project, root, job, as_of=as_of, seed=seed, universe=universe)


def run_all(
    store: Store,
    project: Project,
    root: Path,
    as_of: str | None = None,
    seed: int | None = None,
    sources: bool = True,
    universe: str | None = None,
    only: set[str] | None = None,
    on_job: Any = None,
) -> list[RunResult]:
    """Poll every source, then run every step in dependency order. One pass.

    A replay passes `sources=False`: the raw tables are the record of what arrived,
    and re-polling a live feed during a backtest would overwrite the very history
    being measured.
    """
    results: list[RunResult] = []
    if sources:
        for s in project.sources:
            results.append(run_source(store, project, root, s))
            if on_job:
                on_job(results[-1])
    for step in order(project):
        if only is not None and step.id not in only:
            continue
        results.append(
            run_step(store, project, root, step, as_of=as_of, seed=seed, universe=universe)
        )
        if on_job:
            on_job(results[-1])
    return results


__all__ = ["RunResult", "order", "phys", "run_all", "run_job", "run_source", "run_step"]
