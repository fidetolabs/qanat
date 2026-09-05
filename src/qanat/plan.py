"""What would change if this file were applied.

`qanat check` asks one question: is this file legal? `qanat plan` asks a
different one: **does this file match the database it points at?**

Three kinds of drift show up here, and only the first is obvious:

  create   declared in the file, not in the database yet
  orphan   in the database, and nothing in the file produces it any more
  change   a job whose schedule, script, wiring or options moved since it last ran

The third one needs a memory of what was applied before, which is what
`_qanat_state` holds -- a snapshot of every job's spec, written after a run.
Without it a changed lookback window is invisible: the table still exists, still
has rows, and quietly means something else than it did last week.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from qanat.models import Project, Source, Step
from qanat.store import Store


class _NoRows:
    rows = 0


# --------------------------------------------------------------------- specs
def file_digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def job_spec(job: Source | Step, root: Path) -> dict:
    """Everything about a job that, if it changed, changes what it produces."""
    if isinstance(job, Source):
        return {
            "kind": "source",
            "connector": job.connector,
            "to": sorted(job.writes),
            "schedule": job.schedule,
            "mode": job.mode,
            "options": job.options,
        }
    return {
        "kind": "step",
        "script": job.script,
        "script_digest": file_digest(root / job.script),
        "from": sorted(job.reads),
        "to": sorted(job.writes),
        "schedule": job.schedule,
        "universe": job.universe,
        "options": job.options,
    }


def digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def snapshot(project: Project, root: Path) -> dict[str, tuple[str, str]]:
    """job id -> (digest, spec json), ready for Store.save_state."""
    out = {}
    for job in project.jobs:
        spec = job_spec(job, root)
        out[job.id] = (digest(spec), json.dumps(spec, sort_keys=True))
    return out


# --------------------------------------------------------------------- plan
@dataclass
class Change:
    action: str  # create | orphan | add | remove | update
    target: str
    note: str = ""
    details: list[str] = field(default_factory=list)


@dataclass
class Plan:
    creates: list[Change] = field(default_factory=list)
    orphans: list[Change] = field(default_factory=list)
    adds: list[Change] = field(default_factory=list)
    removes: list[Change] = field(default_factory=list)
    updates: list[Change] = field(default_factory=list)
    unchanged: int = 0
    first_run: bool = False

    @property
    def changes(self) -> list[Change]:
        return [*self.adds, *self.updates, *self.removes, *self.creates, *self.orphans]

    def stale(self, project: Project, store: Store | None = None) -> set[str]:
        """Tables whose rows were computed by something that has since moved.

        Editing a step does not wipe what it wrote -- deleting data because a
        script changed would lose the one copy of a slow computation for no
        reason. The rows simply stop being trustworthy, and say so, until the
        next run. Staleness travels downstream: if a feature is stale, so is
        every weight computed from it.
        """
        moved = {c.target for c in [*self.adds, *self.updates, *self.removes]}
        if not moved:
            return set()
        producer = {ref: j.id for j in project.jobs for ref in j.writes}
        by_id = {j.id: j for j in project.jobs}
        out: set[str] = set()
        for job_id in moved:
            job = by_id.get(job_id)
            if job:
                out.update(job.writes)
        # walk forward until nothing new is added
        changed = True
        while changed:
            changed = False
            for job in project.jobs:
                if any(r in out for r in getattr(job, "reads", [])):
                    for w in job.writes:
                        if w not in out:
                            out.add(w)
                            changed = True
        out = {ref for ref in out if ref in producer}
        if store is None:
            return out
        # A table with no rows is not out of date, it is empty: nothing has computed
        # it yet, so there is no old answer to distrust. Filtering here rather than
        # at each caller is what keeps the console and the agent from disagreeing.
        return {ref for ref in out if (store.table_info(ref) or _NoRows).rows}

    @property
    def empty(self) -> bool:
        return not self.changes


_LABEL = {
    "schedule": "schedule",
    "script": "script",
    "script_digest": "script contents",
    "from": "reads",
    "to": "writes",
    "options": "options",
    "connector": "connector",
    "universe": "universe",
    "mode": "mode",
}


def _diff_specs(old: dict, new: dict) -> list[str]:
    out = []
    for key in sorted(set(old) | set(new)):
        if key == "kind":
            continue
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        label = _LABEL.get(key, key)
        if key == "script_digest":
            out.append(f"{label} changed")
        elif isinstance(a, dict) or isinstance(b, dict):
            for k in sorted(set(a or {}) | set(b or {})):
                if (a or {}).get(k) != (b or {}).get(k):
                    out.append(f"{label}.{k}  {(a or {}).get(k)!r} -> {(b or {}).get(k)!r}")
        else:
            out.append(f"{label}  {a!r} -> {b!r}")
    return out


def _replay_writes(project: Project, store: Store) -> set[str]:
    """Where a backtest lands its results: one table per alpha, plus one for every
    blend that has been run. Derived the same way the engine derives it, so the
    two cannot disagree about which tables are results."""
    stage = next((x for x in project.stages if x.kind == "pnl"), None)
    if stage is None:
        return set()
    keys = [a for a, _ in project.alphas]
    # a store written before blends existed has no rows to add here, and that is
    # the only reason this can come back empty
    keys += [row["alpha"] for row in store.alpha_book() if row.get("alpha")]
    out = set()
    for key in keys:
        ids = [x for x in str(key).split("+") if x]
        if ids:
            out.add(f"{stage.id}." + "_".join(i.removeprefix("alpha_") for i in ids))
    return out


def plan(project: Project, root: Path, store: Store) -> Plan:
    p = Plan()
    state = store.load_state()
    p.first_run = not state

    declared = set(project.tables())
    present = set(store.all_tables())
    producers = project.producers()

    for ref in sorted(declared - present):
        p.creates.append(Change("create", ref, f"from {producers[ref]}"))

    # A PnL table is written by a replay, not by a job, so nothing in the file
    # declares it. Without this it looks like an orphan and `qanat prune` deletes
    # every backtest result the project has -- the one thing in the store that
    # cannot be recomputed by running the pipeline again.
    for ref in sorted(present - declared - _replay_writes(project, store)):
        info = store.table_info(ref)
        rows = f"{info.rows:,} rows" if info else "unknown size"
        p.orphans.append(Change("orphan", ref, f"nothing produces it · {rows}"))

    current = {job.id: job_spec(job, root) for job in project.jobs}
    for job_id, spec in current.items():
        if job_id not in state:
            if not p.first_run:
                p.adds.append(Change("add", job_id, f"new {spec['kind']}"))
            continue
        if state[job_id]["digest"] == digest(spec):
            p.unchanged += 1
            continue
        old = json.loads(state[job_id]["spec"])
        p.updates.append(Change("update", job_id, "", _diff_specs(old, spec)))

    for job_id in sorted(set(state) - set(current)):
        old = json.loads(state[job_id]["spec"])
        still = [t for t in old.get("to", []) if t in present]
        note = f"removed from the file, {', '.join(still)} stays" if still else "removed"
        p.removes.append(Change("remove", job_id, note))

    return p
