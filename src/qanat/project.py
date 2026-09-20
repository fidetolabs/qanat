"""Load a qanat.yaml, and enforce the stage contract.

The contract is the opinion of this tool. It is what stops a pipeline from
becoming a pile of tables nobody can explain:

  1. `raw` is landed and never edited -- no step may write into a raw stage.
  2. Data only ever moves forward. A step may read from its own stage (a feature
     chain) but never from a later one.
  3. There is exactly one `weights` stage. It holds **one table per alpha**, each
     written by exactly one step. No alpha may read another alpha's weights, so no
     edge is ever counted twice.
  4. A `pnl` stage may follow it, and if it exists it is last. Nothing writes it by
     hand: a replay does, one table per alpha, holding what that alpha earned per
     rebalance. It is the end of the pipeline.
  4. Every table read has a producer.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from qanat.context import is_point_in_time
from qanat.models import Project

#: `${name}` in a .sql step body -- the same pattern the runner substitutes with.
_VAR = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


class ProjectError(Exception):
    pass


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def find_project(start: str | os.PathLike[str] | None = None) -> Path:
    """Walk up from `start` looking for qanat.yaml."""
    here = Path(start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        f = cand / "qanat.yaml"
        if f.is_file():
            return f
    raise ProjectError("no qanat.yaml found here or in any parent directory")


def load(path: str | os.PathLike[str] | None = None) -> tuple[Project, Path]:
    """Return the parsed project and its root directory."""
    f = Path(path).resolve() if path else find_project()
    if f.is_dir():
        f = f / "qanat.yaml"
    with f.open() as fh:
        raw = yaml.safe_load(fh) or {}
    return Project.model_validate(raw), f.parent


def validate(p: Project, root: Path) -> Report:
    r = Report()
    stage_ids = [x.id for x in p.stages]

    if len(set(stage_ids)) != len(stage_ids):
        r.errors.append("stage ids must be unique")
    if not p.stages:
        r.errors.append("a project needs at least one stage")

    # ---- rules 3 and 4: weights, then optionally pnl --------------------------
    weights = [x for x in p.stages if x.kind == "weights"]
    pnls = [x for x in p.stages if x.kind == "pnl"]
    if len(weights) != 1:
        r.errors.append(f"exactly one stage must have kind 'weights', found {len(weights)}")
    if len(pnls) > 1:
        r.errors.append(f"at most one stage may have kind 'pnl', found {len(pnls)}")
    last = p.stages[-1].kind if p.stages else None
    if pnls:
        if last != "pnl":
            r.errors.append(f"the pnl stage must be last, but '{p.stages[-1].id}' is")
        if weights and p.stage_index(pnls[0].id) != p.stage_index(weights[0].id) + 1:
            r.errors.append(
                f"'{pnls[0].id}' must come directly after the weights stage "
                f"'{weights[0].id}'. Anything between them is a stage an alpha's output "
                "would have to pass through"
            )
        for st in p.steps:
            if any(w.startswith(f"{pnls[0].id}.") for w in st.writes):
                r.errors.append(
                    f"step '{st.id}' writes into the pnl stage. Nothing writes it by hand: "
                    "a replay does, once it knows what the portfolio earned"
                )
    elif weights and last != "weights":
        r.errors.append(f"the weights stage must be last, but '{p.stages[-1].id}' is")

    producers = p.producers()

    # One table, one producer. `producers()` is a dict, so a second job writing the
    # same table used to disappear from it silently -- and in the weights stage that
    # meant one portfolio counted as two alphas, which is the double counting the
    # whole closure rule exists to prevent.
    writers: dict[str, list[str]] = {}
    for j in p.jobs:
        for ref in j.writes:
            writers.setdefault(ref, []).append(j.id)
    for ref, who in sorted(writers.items()):
        if len(who) > 1:
            r.errors.append(
                f"'{ref}' is written by {' and '.join(sorted(who))}. One table has one "
                "producer, or nothing can say which run made the rows in it"
            )

    if weights:
        wl = weights[0].id
        wtables = [t for t in producers if t.startswith(f"{wl}.")]
        if not wtables:
            r.warnings.append(f"nothing writes into the weights stage '{wl}' yet")
        for st in p.steps:
            mine = [w for w in st.writes if w.startswith(f"{wl}.")]
            if len(mine) > 1:
                r.errors.append(
                    f"step '{st.id}' writes {len(mine)} weights tables. One alpha is one "
                    "portfolio, so one step writes one weights table"
                )
            # the closure rule: an alpha reads features, never another alpha
            if mine and any(rd.startswith(f"{wl}.") for rd in st.reads):
                r.errors.append(
                    f"alpha '{st.id}' reads another alpha's weights. Alphas never stack -- "
                    "an edge counted through a tower of them is counted twice"
                )

    # ---- sources --------------------------------------------------------------
    seen_ids: set[str] = set()
    for s in p.sources:
        if s.id in seen_ids:
            r.errors.append(f"duplicate job id '{s.id}'")
        seen_ids.add(s.id)
        if s.stage not in stage_ids:
            r.errors.append(f"source '{s.id}' writes to unknown stage '{s.stage}'")
        elif (lay := p.stage(s.stage)) and lay.kind == "weights":
            r.errors.append(f"source '{s.id}' writes into the weights stage -- only a step may")
        if s.mode == "append" and not s.key and s.schedule:
            r.warnings.append(
                f"source '{s.id}' appends on a schedule with no `key:`, so every poll keeps a "
                f"full copy of whatever the feed answers. Name the columns that identify one "
                f"row, or set `mode: replace`"
            )

    # ---- steps ----------------------------------------------------------------
    for st in p.steps:
        if st.id in seen_ids:
            r.errors.append(f"duplicate job id '{st.id}'")
        seen_ids.add(st.id)

        if not st.writes:
            r.errors.append(f"step '{st.id}' writes nothing")
        script = root / st.script
        try:
            inside = script.resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):  # a path that cannot be resolved is not inside
            inside = False
        if not inside:
            r.errors.append(
                f"step '{st.id}': script '{st.script}' is outside the project. A step runs "
                "the file it names, so it has to be a path under the project directory"
            )
        elif not script.is_file():
            r.errors.append(f"step '{st.id}': script not found at {st.script}")
        elif script.suffix not in (".sql", ".py"):
            r.errors.append(f"step '{st.id}': script must be .sql or .py, got {script.suffix}")
        elif script.suffix == ".sql" and len(st.writes) != 1:
            r.errors.append(f"step '{st.id}': a .sql step writes exactly one table")

        if st.universe and p.universe(st.universe) is None:
            r.errors.append(f"step '{st.id}' uses unknown universe '{st.universe}'")
        if not st.universe and script.is_file() and script.suffix == ".py":
            try:
                if "ctx.universe(" in script.read_text():
                    r.errors.append(
                        f"step '{st.id}' calls ctx.universe() but has no `universe:` set. "
                        f"Every alpha on the shelf does, so this fails on the first run "
                        f"with 'has no universe set'"
                    )
            except OSError:
                pass

        for ref in st.reads:
            lid = ref.split(".")[0]
            if lid not in stage_ids:
                r.errors.append(f"step '{st.id}' reads unknown stage '{lid}'")
            if ref not in producers:
                r.errors.append(f"step '{st.id}' reads '{ref}', which nothing produces")

        for ref in st.writes:
            lid = ref.partition(".")[0]
            lay = p.stage(lid)
            if lay is None:
                r.errors.append(f"step '{st.id}' writes unknown stage '{lid}'")
                continue
            # rule 1
            if lay.kind == "raw":
                r.errors.append(
                    f"step '{st.id}' writes into raw stage '{lid}'. "
                    "raw is landed as it arrived and never edited"
                )
            # rule 2
            ti = p.stage_index(lid)
            for src in st.reads:
                si = p.stage_index(src.split(".")[0])
                if si > ti:
                    r.errors.append(
                        f"step '{st.id}' reads '{src}' from a later stage than it writes "
                        f"('{ref}'). Data only moves forward"
                    )
                elif si == ti and lay.kind != "features":
                    r.errors.append(
                        f"step '{st.id}' reads and writes the same stage '{lid}'. "
                        "only a features stage may chain"
                    )
            if ref in st.reads:
                r.errors.append(f"step '{st.id}' reads and writes the same table '{ref}'")

        if st.schedule:
            from croniter import croniter

            if not croniter.is_valid(st.schedule):
                r.errors.append(f"step '{st.id}': '{st.schedule}' is not a valid cron expression")

    for s in p.sources:
        if s.schedule:
            from croniter import croniter

            if not croniter.is_valid(s.schedule):
                r.errors.append(f"source '{s.id}': '{s.schedule}' is not a valid cron expression")

    for b in p.universes:
        if not (root / b.symbols).is_file():
            r.errors.append(f"universe '{b.id}': symbols file not found at {b.symbols}")

    from qanat.retention import parse_duration

    for job in p.jobs:
        if job.timeout:
            try:
                parse_duration(job.timeout, "timeout")
            except ValueError as exc:
                r.errors.append(f"job '{job.id}': {exc}")
    if p.job_timeout:
        try:
            parse_duration(p.job_timeout, "job_timeout")
        except ValueError as exc:
            r.errors.append(str(exc))

    # A file store outside the project is not wrong, but it is worth saying out loud:
    # the project and its data then move separately, and a copy of the directory is
    # no longer a copy of the work.
    if not p.store.startswith(("postgresql://", "postgres://")):
        try:
            inside = (root / p.store).resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):
            inside = False
        if not inside:
            r.warnings.append(
                f"store '{p.store}' is outside the project directory, so copying or moving "
                f"the project will not take its data along"
            )

    for ref, policy in p.retention.items():
        if ref.count(".") != 1:
            r.errors.append(f"retention key must be stage.table, got {ref!r}")
        else:
            lid = ref.split(".")[0]
            if lid not in stage_ids:
                r.errors.append(f"retention '{ref}' references unknown stage '{lid}'")
            try:
                from qanat.retention import MIN_RETENTION

                d = parse_duration(policy)
                if d < MIN_RETENTION:
                    r.errors.append(
                        f"retention '{ref}': {policy!r} is shorter than the one-hour floor. "
                        f"'1s' and '1d' are one keystroke apart and this deletes rows"
                    )
                elif (lay := p.stage(lid)) and lay.kind == "raw":
                    r.warnings.append(
                        f"retention '{ref}' deletes rows from a raw stage. raw is the record of "
                        f"what arrived, and it is the one thing a replay cannot rebuild -- every "
                        f"other table is a function of it"
                    )
            except ValueError as exc:
                r.errors.append(f"retention '{ref}': {exc}")

    # ---- backtest -------------------------------------------------------------
    if p.backtest is not None:
        bt = p.backtest
        if bt.prices not in producers:
            r.errors.append(
                f"backtest prices '{bt.prices}' is not produced by anything in this project"
            )
        for field_name in ("rebalance", "purge", "embargo"):
            try:
                parse_duration(getattr(bt, field_name))
            except ValueError as exc:
                r.errors.append(f"backtest {field_name}: {exc}")
        if bt.fee_bps < 0 or bt.slippage_bps < 0:
            r.errors.append("backtest fee_bps and slippage_bps cannot be negative")
        # A name with a typo in it would stop live scoring dead, and the only sign
        # would be a line in the event log of a server nobody is watching.
        for name in bt.live_alphas:
            if p.alpha(name) is None:
                known = ", ".join(a for a, _ in p.alphas) or "(none yet)"
                r.errors.append(
                    f"backtest live_alphas names '{name}', which is not an alpha here. "
                    f"This project has: {known}"
                )
        if bt.live and not bt.live_alphas and len(p.alphas) > 1:
            r.warnings.append(
                f"live is on and this project has {len(p.alphas)} alphas, but `live_alphas:` "
                f"names none of them, so nothing will be scored forward. Name one (or several, "
                f"to hold them as one book)"
            )
        if weights and not [t for t in producers if t.startswith(f"{weights[0].id}.")]:
            r.warnings.append("backtest is configured but nothing writes a portfolio to price")

    for ref in p.time_columns:
        if ref.count(".") != 1:
            r.errors.append(f"time_columns key must be stage.table, got {ref!r}")
        elif ref not in producers:
            r.warnings.append(f"time_columns names '{ref}', which nothing produces")

    # A universe with no join and leave dates is today's list applied to the past.
    # Every number built on it is flattered by the names that survived, so the file
    # should say so out loud rather than let a good-looking figure go unqualified.
    for u in p.universes:
        path = root / u.symbols
        if not path.is_file():
            r.errors.append(f"universe '{u.id}': symbols file not found at {u.symbols}")
            continue
        try:
            head = pd.read_csv(path, nrows=1)
        except Exception as exc:  # noqa: BLE001 - a bad csv is the user's to see
            r.errors.append(f"universe '{u.id}': {type(exc).__name__} reading {u.symbols}")
            continue
        if "symbol" not in {c.lower() for c in head.columns}:
            r.errors.append(f"universe '{u.id}': {u.symbols} needs a 'symbol' column")
        elif not is_point_in_time(head):
            r.warnings.append(
                f"universe '{u.id}' has no membership dates, so every backtest holds "
                f"today's list across the whole window -- survivorship bias. Add `from` "
                f"and `to` columns to {u.symbols} to price what was really investable"
            )

    # ---- the graph has to be a graph ------------------------------------------
    # `runner.order()` gives up on a cycle and appends what is left in file order,
    # with a comment saying `qanat check` reports it. It did not. On an empty store
    # the run fails with a confusing LookupError; on a populated one every step
    # reports ok and the values grow on every pass, so the answer depends on how
    # many times the pipeline was run.
    remaining = list(p.steps)
    produced = {s.ref for s in p.sources}
    while True:
        ready = [s for s in remaining if all(rd in produced for rd in s.reads)]
        if not ready:
            break
        for s in ready:
            produced.update(s.writes)
            remaining.remove(s)
    stuck = [s for s in remaining if all(rd in producers for rd in s.reads)]
    if stuck:
        r.errors.append(
            "these steps depend on each other in a loop, so nothing can run first: "
            + ", ".join(sorted(s.id for s in stuck))
        )

    # ---- a ${name} in a .sql step needs an option behind it --------------------
    # Left unresolved the text stays in the query as a valid string literal that
    # matches nothing, so a typo in a YAML key empties the table and reports ok.
    for st in p.steps:
        f = root / st.script
        if f.suffix != ".sql" or not f.is_file():
            continue
        try:
            body = f.read_text()
        except OSError:
            continue
        from qanat.store import tables_named

        stray = sorted(
            ref for ref in tables_named(body)
            if ref in producers and ref not in st.reads and ref not in st.writes
        )
        if stray:
            r.errors.append(
                f"step '{st.id}': {Path(st.script).name} reads {', '.join(stray)} without "
                f"declaring {'it' if len(stray) == 1 else 'them'} in `from:`. The graph is "
                f"drawn from what a step declares, so an undeclared read is an arrow nobody "
                f"can see -- and `qanat plan` will not mark this step stale when that table "
                f"changes"
            )

        for name in sorted(set(_VAR.findall(body))):
            if name == "as_of":
                continue
            if name not in st.options:
                r.errors.append(
                    f"step '{st.id}': {Path(st.script).name} uses ${{{name}}}, which is not in "
                    f"its options ({', '.join(sorted(st.options)) or 'none set'})"
                )
        if "as_of" in st.options:
            r.warnings.append(
                f"step '{st.id}' sets an option called 'as_of'. A replay overwrites it with "
                "the date being replayed, so this step means something different under "
                "`qanat backtest` than under `qanat run`"
            )

    # ---- soft advice ----------------------------------------------------------
    for ref, who in producers.items():
        read_by = [st.id for st in p.steps if ref in st.reads]
        lid = ref.split(".")[0]
        lay = p.stage(lid)
        if not read_by and lay and lay.kind != "weights":
            r.warnings.append(f"'{ref}' (from {who}) is never read by anything")

    return r


def edges(p: Project) -> list[tuple[str, str, str]]:
    """(from_table, to_table, step_id) for every path a row can take."""
    out = []
    for st in p.steps:
        for a in st.reads:
            for b in st.writes:
                out.append((a, b, st.id))
    return out
