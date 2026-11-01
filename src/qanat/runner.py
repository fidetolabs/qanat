"""Running one job -- an ingestion poll, or a transform.

**A job is one definition for one exact datetime, and an execution is one attempt
at it.** Every execution writes a row before it starts and after it ends, so
anything asking what happened is reading history rather than being told a story.
Three attempts at the 07:00 poll are one job and three executions, which is what
makes *"did the 07:00 poll succeed?"* a question with an object to ask.

An ad-hoc call opens a job too, with no schedule behind it. That is the honest
record of a hand-triggered pass, and it is first-class rather than an exception.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import random
import re
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qanat import connectors
from qanat.context import Context
from qanat.models import (
    Definition,
    IngestionJobDefinition,
    Project,
    TransformJobDefinition,
)
from qanat.store import Store, phys

_VAR = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


def _record_applied(store: Store, root: Path, definition: Definition) -> None:
    """What this definition looked like the last time it worked -- the basis
    `qanat plan` compares against, and the version a row's lineage resolves to.
    Written only on success, so a failed execution never becomes the thing a
    later change is measured from."""
    from qanat.plan import definition_spec, digest

    spec = definition_spec(definition, root)
    store.set_state(definition.id, digest(spec), json.dumps(spec, sort_keys=True))


def _open(store: Store, definition: Definition, kind: str, targets: list[str],
          scheduled_for: Any = None, schedule_id: str | None = None) -> tuple[int, int]:
    """The job for this instant, and a fresh execution of it.

    `scheduled_for` unset means now, which is the ad-hoc path: each call is its
    own job. The scheduler passes the cron instant instead, so firing the same
    instant twice adds an attempt to the job that is already there.
    """
    when = scheduled_for if scheduled_for is not None else datetime.now(timezone.utc).replace(tzinfo=None)
    job_id = store.open_job(definition.id, kind, when, schedule_id)
    return job_id, store.start_execution(job_id, definition.id, kind, targets)


@dataclass
class RunResult:
    definition_id: str
    status: str
    rows: int = 0
    error: str | None = None
    targets: tuple[str, ...] = ()
    #: Which job this was an execution of, and which execution. Carried out so a
    #: caller can hand a row's provenance back to `lineage` without a second read.
    job_id: int | None = None
    execution_id: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


# ------------------------------------------------------------------- ingestion
def run_ingestion(store: Store, project: Project, root: Path,
                  src: IngestionJobDefinition, scheduled_for: Any = None,
                  schedule_id: str | None = None) -> RunResult:
    job_id, execution = _open(store, src, "ingestion", [src.ref], scheduled_for, schedule_id)
    try:
        # What is the newest row this table already holds? An adapter that can
        # fetch incrementally uses it to ask only for what came after.
        since = None
        if store.exists(src.ref):
            with contextlib.suppress(Exception):
                since = store.max_time(src.ref, project.time_columns.get(src.ref))
        df = connectors.fetch(
            src, root, on_warn=lambda m: store.event("warn", src.id, m), since=since
        )
        if df is None or df.empty:
            # An empty answer leaves the table alone. It used to clear it in replace
            # mode, which was correct for that mode and is the reason the mode is
            # gone: the rows an ingestion job landed are the ones nothing else can rebuild,
            # so a poll that returns nothing says "nothing new", never "there was
            # nothing".
            _record_applied(store, root, src)
            store.event("warn", src.id, "the connector returned no rows")
            store.end_execution(execution, "ok", 0)
            return RunResult(src.id, "ok", 0, targets=(src.ref,),
                             job_id=job_id, execution_id=execution)
        rows = store.write(src.ref, df, mode=src.mode, key=src.key)
        _record_applied(store, root, src)
        store.event("ok", src.id, f"landed {rows:,} rows in {src.ref}")
        store.end_execution(execution, "ok", rows)
        return RunResult(src.id, "ok", rows, targets=(src.ref,),
                         job_id=job_id, execution_id=execution)
    except Exception as exc:  # noqa: BLE001 -- one failing job must not stop the loop
        msg = f"{type(exc).__name__}: {exc}"
        store.event("error", src.id, msg)
        store.end_execution(execution, "failed", 0, msg)
        return RunResult(src.id, "failed", 0, msg, (src.ref,),
                         job_id=job_id, execution_id=execution)
    except BaseException as exc:
        # `sys.exit()` or Ctrl-C. Not an Exception, so it used to skip the close and
        # leave the row saying `running` for ever. Record it, then let it through.
        store.event("error", src.id, f"interrupted -- {type(exc).__name__}"[:400])
        store.end_execution(execution, "interrupted", 0, str(exc)[:2000])
        raise


# ------------------------------------------------------------------ transforms
def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(f"qanat_transform_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import transform script {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed(seed: int | None) -> None:
    """Pin every generator a transform might reach for, so a screen repeats."""
    if seed is None:
        return
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed % (2**32))
    except ImportError:  # pragma: no cover -- numpy is a dependency, but do not require it here
        pass


def _run_sql(store: Store, ctx: Context, transform: TransformJobDefinition, script: Path) -> int:
    sql = script.read_text()
    names: dict[str, Any] = {**transform.options}
    if ctx.as_of:
        names["as_of"] = ctx.as_of
    sql = _VAR.sub(lambda m: str(names.get(m.group(1), m.group(0))), sql)
    if transform.universe:
        store.con.register("universe", ctx.universe())
    try:
        body = sql.strip().rstrip(";")
        if transform.key:
            # A keyed transform adds rows and never replaces one. See
            # `TransformJobDefinition.key`.
            return store.sql_append(transform.writes[0], body, transform.key)
        return store.sql_into(transform.writes[0], body)
    finally:
        if transform.universe:
            store.con.unregister("universe")


def _run_python(store: Store, ctx: Context, transform: TransformJobDefinition, script: Path) -> int:
    mod = _load_module(script)
    if not hasattr(mod, "run"):
        raise AttributeError(f"{script.name} must define run(ctx)")
    out = mod.run(ctx)

    if isinstance(out, pd.DataFrame):
        if len(transform.writes) != 1:
            raise ValueError(
                f"transform '{transform.id}' writes {len(transform.writes)} tables, so run(ctx) must return "
                "a dict of {table: dataframe}"
            )
        out = {transform.writes[0]: out}
    if not isinstance(out, dict):
        raise TypeError(f"transform '{transform.id}': run(ctx) must return a DataFrame or a dict of them")

    total = 0
    declared = {w.partition(".")[2]: w for w in transform.writes}
    # Check the whole set before writing any of it. Validating as we went meant a
    # transform that failed halfway left the tables it had already written, so the graph
    # was half updated while the transform was marked failed.
    unknown = [k for k in out if k not in transform.writes and k not in declared]
    if unknown:
        raise KeyError(
            f"transform '{transform.id}' returned '{unknown[0]}', which it did not declare in writes "
            f"({', '.join(transform.writes)})"
        )
    missing_up_front = set(transform.writes) - {k if k in transform.writes else declared.get(k) for k in out}
    if missing_up_front:
        raise ValueError(
            f"transform '{transform.id}' declared but did not write: {', '.join(sorted(missing_up_front))}"
        )
    for key, df in out.items():
        ref = key if key in transform.writes else declared.get(key)
        if ref is None:
            raise KeyError(
                f"transform '{transform.id}' returned '{key}', which it did not declare in writes "
                f"({', '.join(transform.writes)})"
            )
        total += store.write(ref, df, mode="append" if transform.key else "replace",
                             key=transform.key)
    return total


def _check_lookahead(store: Store, project: Project, transform: TransformJobDefinition, as_of: str) -> None:
    """A screened transform may not write a row stamped after the moment it ran.

    The as-of views already keep the future out of what a transform reads. This catches
    the other way in -- a transform that built a timestamp itself, or read around the
    views with raw SQL -- and it fails the transform rather than warning, because a
    number computed from the future is worse than no number.
    """
    import pandas as pd

    cut = pd.Timestamp(as_of)
    for ref in transform.writes:
        col = store.time_column(ref, project.time_columns.get(ref))
        if col is None:
            # Nothing to check against: this table has no clock, so the as-of views
            # do not filter it either. Silence made the guard look wider than it is.
            store.event("warn", transform.id, (
                f"{ref} has no time column, so it was not checked for lookahead and the "
                f"as-of views do not filter it. Set time_columns['{ref}'] if a column "
                f"there carries the date under another name"
            ))
            continue
        newest = store.max_time(ref, project.time_columns.get(ref))
        if newest is None:
            continue
        if pd.Timestamp(newest) > cut:
            raise ValueError(
                f"lookahead: '{transform.id}' wrote {ref} with {col} = {newest}, "
                f"which is after the as-of date {as_of}"
            )


def run_transform(
    store: Store,
    project: Project,
    root: Path,
    transform: TransformJobDefinition,
    as_of: str | None = None,
    seed: int | None = None,
    universe: str | None = None,
    scheduled_for: Any = None,
    schedule_id: str | None = None,
) -> RunResult:
    job_id, execution = _open(store, transform, "transform", list(transform.writes),
                           scheduled_for, schedule_id)
    ctx = Context(store, project, root, transform, as_of=as_of, universe=universe)
    script = root / transform.script
    try:
        missing = [r for r in transform.reads if not store.exists(r)]
        if missing:
            raise LookupError(f"upstream table(s) not there yet: {', '.join(missing)}")

        _seed(seed)
        rows = (
            _run_sql(store, ctx, transform, script)
            if script.suffix == ".sql"
            else _run_python(store, ctx, transform, script)
        )
        if as_of:
            _check_lookahead(store, project, transform, as_of)
        _record_applied(store, root, transform)
        store.event("ok", transform.id, f"wrote {rows:,} rows to {', '.join(transform.writes)}")
        store.end_execution(execution, "ok", rows)
        return RunResult(transform.id, "ok", rows, targets=tuple(transform.writes),
                         job_id=job_id, execution_id=execution)
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        store.event("error", transform.id, msg.splitlines()[0][:400])
        store.end_execution(execution, "failed", 0, traceback.format_exc(limit=3)[:2000])
        return RunResult(transform.id, "failed", 0, msg, tuple(transform.writes),
                         job_id=job_id, execution_id=execution)
    except BaseException as exc:
        # `sys.exit()` in a script, or Ctrl-C. These are not Exceptions, so they used
        # to sail past the close and leave the row saying `running` for ever, and
        # every later reader believed it. Record, then re-raise.
        msg = f"{type(exc).__name__}: {exc}"
        store.event("error", transform.id, f"interrupted -- {msg}"[:400])
        store.end_execution(execution, "interrupted", 0, msg[:2000])
        raise


# ------------------------------------------------------------------- the graph
def order(project: Project) -> list[TransformJobDefinition]:
    """Transforms, sorted so each one runs after everything it reads."""
    remaining = list(project.transform_job_definitions)
    produced = {d.ref for d in project.ingestion_job_definitions}
    out: list[TransformJobDefinition] = []
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


def run_definition(
    store: Store,
    project: Project,
    root: Path,
    definition_id: str,
    as_of: str | None = None,
    seed: int | None = None,
    universe: str | None = None,
    scheduled_for: Any = None,
    schedule_id: str | None = None,
) -> RunResult:
    """One definition, now. Either kind -- the definition says which it is."""
    definition = project.definition(definition_id)
    if definition is None:
        raise KeyError(f"no job definition called '{definition_id}'")
    if isinstance(definition, IngestionJobDefinition):
        if as_of:
            raise ValueError(
                f"'{definition_id}' is an ingestion job. A screen reads what already "
                "landed; it never polls the outside world again"
            )
        return run_ingestion(store, project, root, definition,
                             scheduled_for=scheduled_for, schedule_id=schedule_id)
    return run_transform(store, project, root, definition, as_of=as_of, seed=seed,
                         universe=universe, scheduled_for=scheduled_for,
                         schedule_id=schedule_id)


def run_all(
    store: Store,
    project: Project,
    root: Path,
    as_of: str | None = None,
    seed: int | None = None,
    ingest: bool = True,
    universe: str | None = None,
    only: set[str] | None = None,
    on_job: Any = None,
) -> list[RunResult]:
    """Every ingestion job, then every transform in dependency order. One pass.

    A screen passes `ingest=False`: the raw tables are the record of what arrived,
    and re-polling a live feed during a screen would overwrite the very history
    being measured.
    """
    results: list[RunResult] = []
    if ingest:
        for d in project.ingestion_job_definitions:
            results.append(run_ingestion(store, project, root, d))
            if on_job:
                on_job(results[-1])
    # Tables whose writer failed this pass. A transform reading one of them would
    # recompute from the last good version, report `ok`, and stamp itself as applied
    # -- so one vendor failure left a store that looked green everywhere with
    # mixed-vintage data underneath.
    broken: set[str] = {w for r in results if not r.ok for w in r.targets}
    for transform in order(project):
        if only is not None and transform.id not in only:
            continue
        stale_inputs = sorted(set(transform.reads) & broken)
        if stale_inputs:
            store.event("warn", transform.id,
                        f"skipped -- {', '.join(stale_inputs)} did not refresh this pass")
            results.append(RunResult(transform.id, "skipped", 0,
                                     f"upstream did not refresh: {', '.join(stale_inputs)}",
                                     tuple(transform.writes)))
            broken.update(transform.writes)
            if on_job:
                on_job(results[-1])
            continue
        results.append(
            run_transform(store, project, root, transform, as_of=as_of, seed=seed,
                          universe=universe)
        )
        if not results[-1].ok:
            broken.update(results[-1].targets)
        if on_job:
            on_job(results[-1])
    return results


__all__ = ["RunResult", "order", "phys", "run_all", "run_definition", "run_ingestion",
           "run_transform"]
