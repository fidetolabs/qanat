"""What would change if this file were applied.

`qanat check` asks one question: is this file legal? `qanat plan` asks a
different one: **does this file match the database it points at?**

Three kinds of drift show up here, and only the first is obvious:

  create   declared in the file, not in the database yet
  orphan   in the database, and nothing in the file produces it any more
  change   a definition or a schedule that moved since it last ran

The third one needs a memory of what was applied before, which is what
`_qanat_job_definition_version` holds -- a snapshot of every definition's spec,
written after a run. Without it a changed lookback window is invisible: the
table still exists, still has rows, and quietly means something else than it did
last week.

**A schedule is tracked beside the definitions and not inside them.** Its cron
moving is drift in its own right -- a daily poll that quietly became monthly
produces exactly the rows it used to, a month apart -- and a definition that
carries no cadence cannot report it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from qanat.models import Definition, IngestionJobDefinition, JobSchedule, Project
from qanat.store import Store


class _NoRows:
    rows = 0


# --------------------------------------------------------------------- specs
def file_digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def definition_spec(definition: Definition, root: Path) -> dict:
    """Everything about a definition that, if it changed, changes what it produces.

    **No cadence in here**, because a definition has none. How often it fires is
    a schedule, and a schedule gets its own spec.
    """
    if isinstance(definition, IngestionJobDefinition):
        return {
            "kind": "ingestion_job_definition",
            "connector": definition.connector,
            "to": sorted(definition.writes),
            "mode": definition.mode,
            "key": sorted(definition.key),
            "timeout": definition.timeout,
            "options": definition.options,
        }
    return {
        "kind": "transform_job_definition",
        "script": definition.script,
        "script_digest": file_digest(root / definition.script),
        "from": sorted(definition.reads),
        "to": sorted(definition.writes),
        "key": sorted(definition.key),
        "options": definition.options,
        "when": sorted(definition.when),
        "timeout": definition.timeout,
    }


def schedule_spec(schedule: JobSchedule) -> dict:
    """A cadence, as its own tracked thing."""
    return {
        "kind": "job_schedule",
        "definition": schedule.definition,
        "cron": schedule.cron,
        "enabled": schedule.enabled,
    }


def spec_of(obj: Definition | JobSchedule, root: Path) -> dict:
    return (schedule_spec(obj) if isinstance(obj, JobSchedule)
            else definition_spec(obj, root))


def digest(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def tracked(project: Project) -> list[Definition | JobSchedule]:
    """Everything `plan` watches for drift: both kinds of definition, and every
    schedule. One flat id space, which `validate` keeps unique."""
    return [*project.definitions, *project.job_schedules]


def snapshot(project: Project, root: Path) -> dict[str, tuple[str, str]]:
    """id -> (digest, spec json), ready for Store.save_definition_version."""
    out = {}
    for obj in tracked(project):
        spec = spec_of(obj, root)
        out[obj.id] = (digest(spec), json.dumps(spec, sort_keys=True))
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

        Editing a transform does not wipe what it wrote -- deleting data because a
        script changed would lose the one copy of a slow computation for no
        reason. The rows simply stop being trustworthy, and say so, until the
        next run. Staleness travels downstream: if a feature is stale, so is
        every weight computed from it.
        """
        moved = {c.target for c in [*self.adds, *self.updates, *self.removes]}
        if not moved:
            return set()
        written = project.writers()
        by_id = {d.id: d for d in project.definitions}
        # A schedule moving does not make a table stale: the same definition
        # produced the rows, and the only thing that changed is when it fires
        # next. Reporting it as staleness would mark a whole graph out of date
        # for a changed hour.
        out: set[str] = set()
        for did in moved:
            definition = by_id.get(did)
            if definition:
                out.update(definition.writes)
        # walk forward until nothing new is added
        changed = True
        while changed:
            changed = False
            for definition in project.definitions:
                if any(r in out for r in definition.reads):
                    for w in definition.writes:
                        if w not in out:
                            out.add(w)
                            changed = True
        out = {ref for ref in out if ref in written}
        if store is None:
            return out
        # A table with no rows is not out of date, it is empty: nothing has computed
        # it yet, so there is no old answer to distrust. Filtering here rather than
        # at each caller is what keeps the CLI and the agent from disagreeing.
        return {ref for ref in out if (store.table_info(ref) or _NoRows).rows}

    @property
    def empty(self) -> bool:
        return not self.changes


_LABEL = {
    "cron": "cron",
    "definition": "definition",
    "enabled": "enabled",
    "script": "script",
    "script_digest": "script contents",
    "from": "reads",
    "to": "writes",
    "options": "options",
    "connector": "connector",
    "mode": "mode",
    "key": "key",
    "when": "waits on",
    "timeout": "timeout",
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


def plan(project: Project, root: Path, store: Store) -> Plan:
    p = Plan()
    state = store.load_state()
    p.first_run = not state

    declared = set(project.tables())
    present = set(store.all_tables())
    writers = project.writers()

    for ref in sorted(declared - present):
        p.creates.append(Change("create", ref, f"from {', '.join(writers[ref])}"))

    for ref in sorted(present - declared):
        info = store.table_info(ref)
        rows = f"{info.rows:,} rows" if info else "unknown size"
        p.orphans.append(Change("orphan", ref, f"nothing produces it · {rows}"))

    current = {obj.id: spec_of(obj, root) for obj in tracked(project)}
    for oid, spec in current.items():
        if oid not in state:
            if not p.first_run:
                p.adds.append(Change("add", oid, f"new {spec['kind']}"))
            continue
        if state[oid]["digest"] == digest(spec):
            p.unchanged += 1
            continue
        old = json.loads(state[oid]["spec"])
        p.updates.append(Change("update", oid, "", _diff_specs(old, spec)))

    for oid in sorted(set(state) - set(current)):
        old = json.loads(state[oid]["spec"])
        still = [t for t in old.get("to", []) if t in present]
        note = f"removed from the file, {', '.join(still)} stays" if still else "removed"
        p.removes.append(Change("remove", oid, note))

    return p
