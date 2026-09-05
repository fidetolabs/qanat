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
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from qanat.context import is_point_in_time
from qanat.models import Project


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
        if weights and p.stage_index(pnls[0].id) < p.stage_index(weights[0].id):
            r.errors.append("the pnl stage comes after weights -- it is what a replay earned")
        for st in p.steps:
            if any(w.startswith(f"{pnls[0].id}.") for w in st.writes):
                r.errors.append(
                    f"step '{st.id}' writes into the pnl stage. Nothing writes it by hand: "
                    "a replay does, once it knows what the portfolio earned"
                )
    elif weights and last != "weights":
        r.errors.append(f"the weights stage must be last, but '{p.stages[-1].id}' is")

    producers = p.producers()

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

    # ---- steps ----------------------------------------------------------------
    for st in p.steps:
        if st.id in seen_ids:
            r.errors.append(f"duplicate job id '{st.id}'")
        seen_ids.add(st.id)

        if not st.writes:
            r.errors.append(f"step '{st.id}' writes nothing")
        script = root / st.script
        if not script.is_file():
            r.errors.append(f"step '{st.id}': script not found at {st.script}")
        elif script.suffix not in (".sql", ".py"):
            r.errors.append(f"step '{st.id}': script must be .sql or .py, got {script.suffix}")
        elif script.suffix == ".sql" and len(st.writes) != 1:
            r.errors.append(f"step '{st.id}': a .sql step writes exactly one table")

        if st.universe and p.universe(st.universe) is None:
            r.errors.append(f"step '{st.id}' uses unknown universe '{st.universe}'")

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

    for ref, policy in p.retention.items():
        if ref.count(".") != 1:
            r.errors.append(f"retention key must be stage.table, got {ref!r}")
        else:
            lid = ref.split(".")[0]
            if lid not in stage_ids:
                r.errors.append(f"retention '{ref}' references unknown stage '{lid}'")
            try:
                parse_duration(policy)
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
