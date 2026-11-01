"""Read a project written in the old vocabulary, and write it in the new one.

`sources:` became `ingestion_job_definitions:`, `steps:` became
`transform_job_definitions:`, and the `schedule:` field on either one became a
`job_schedules:` row pointing at it by id.

**Why a migration rather than an alias.** An alias leaves both vocabularies in
the record, which is the state the rename exists to end -- and a `schedule:`
field quietly read as a schedule row would be a *different* pipeline than the
one on the page, because the row can be joined, disabled and duplicated and the
field could not. So the old spellings are refused at load, and this is the one
thing that reads them.

**It is a verb a person runs**, which is why it is a fifth command on a CLI that
is otherwise four. It writes files once, on a project somebody owns, from a
shell -- and doing it silently on load is exactly what the architecture-file rule
forbids.

What it does not do: guess. A `schedule:` becomes a schedule row with an id made
from the definition's, and nothing else moves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Keys that mean the project predates the split. Any one of them is enough.
OLD_KEYS = ("sources", "steps")


class MigrateError(Exception):
    pass


@dataclass
class Migration:
    path: Path
    #: What moved, in the order it moved, for printing.
    notes: list[str] = field(default_factory=list)
    #: The new document. Written only if `apply` is called.
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def needed(self) -> bool:
        return bool(self.notes)


def looks_old(raw: dict[str, Any]) -> bool:
    return any(k in raw for k in OLD_KEYS)


def schedule_id_for(definition_id: str) -> str:
    """One schedule per migrated field, named after the definition it came from.

    Deterministic, so running the migration twice would name the same row rather
    than growing a second one.
    """
    return f"{definition_id}_cron"[:60]


def plan_migration(path: str | os.PathLike[str]) -> Migration:
    """What would change. Reads, writes nothing."""
    f = Path(path).resolve()
    if f.is_dir():
        f = f / "qanat.yaml"
    if not f.is_file():
        raise MigrateError(f"no qanat.yaml at {f}")
    with f.open() as fh:
        raw = yaml.safe_load(fh) or {}
    m = Migration(path=f)
    if not looks_old(raw):
        return m

    out: dict[str, Any] = {}
    schedules: list[dict[str, Any]] = []
    for key, new_key, what in (("sources", "ingestion_job_definitions", "ingestion job"),
                               ("steps", "transform_job_definitions", "transform")):
        entries = raw.get(key)
        if entries is None:
            continue
        moved = []
        for entry in entries:
            one = dict(entry or {})
            cron = one.pop("schedule", None)
            # `mode: replace` on a source deletes every row outside the current
            # selection. It is refused by the model, so leaving it here would
            # produce a file that still cannot be loaded -- and the honest fix is
            # the one the refusal names.
            if key == "sources" and one.get("mode") == "replace":
                one["mode"] = "append"
                m.notes.append(
                    f"{what} '{one.get('id')}': mode replace -> append. Replace rewrote "
                    f"{', '.join(one.get('to') or []) or 'its table'} to only what the poll "
                    f"returned, deleting every row landed outside the selection"
                )
                if not one.get("key"):
                    m.notes.append(
                        f"{what} '{one.get('id')}': no `key:`, so an append keeps a full copy "
                        f"of every poll. Name the columns that identify one row"
                    )
            if key == "steps" and str(one.get("script", "")).startswith("steps/"):
                was = one["script"]
                one["script"] = "transforms/" + was[len("steps/"):]
                m.notes.append(f"{what} '{one.get('id')}': script {was} -> {one['script']}")
            if cron:
                sid = schedule_id_for(str(one.get("id")))
                schedules.append({"id": sid, "definition": one.get("id"), "cron": cron})
                m.notes.append(
                    f"{what} '{one.get('id')}': schedule '{cron}' -> job_schedules entry "
                    f"'{sid}'"
                )
            moved.append(one)
        out[new_key] = moved
        m.notes.append(f"{key}: -> {new_key}: ({len(moved)} entr{'y' if len(moved) == 1 else 'ies'})")

    # Rebuilt in the order the new file reads in, so the result is a file a
    # person can still find their way around.
    new: dict[str, Any] = {}
    for key, value in raw.items():
        if key in OLD_KEYS:
            if key == "sources" and "ingestion_job_definitions" in out:
                new["ingestion_job_definitions"] = out["ingestion_job_definitions"]
            if key == "steps" and "transform_job_definitions" in out:
                new["transform_job_definitions"] = out["transform_job_definitions"]
                if schedules:
                    new["job_schedules"] = schedules
                    schedules = []
            continue
        new[key] = value
    if schedules:
        new["job_schedules"] = schedules
    m.data = new
    return m


def apply_migration(m: Migration) -> list[Path]:
    """Write the new file, keeping the old one beside it.

    The backup is not politeness. The round-tripper cannot carry a comment from a
    key that no longer exists, so a note written above `sources:` has nowhere to
    land -- and the one place it survives is the file it was written in.
    """
    if not m.needed:
        return []
    backup = m.path.with_suffix(".yaml.before-the-job-model")
    backup.write_text(m.path.read_text())

    from qanat.project_io import render_project

    text = render_project(m.data, m.path)
    tmp = m.path.with_suffix(".yaml.tmp")
    with tmp.open("w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, m.path)

    moved = []
    old_dir = m.path.parent / "steps"
    new_dir = m.path.parent / "transforms"
    if old_dir.is_dir() and not new_dir.exists():
        old_dir.rename(new_dir)
        moved.append(new_dir)
    return [m.path, backup, *moved]


__all__ = ["MigrateError", "Migration", "apply_migration", "looks_old", "plan_migration"]
