"""Read a project written the old way, and write it the new way.

Two changes, one command, because a project that needs either usually needs both.

**The vocabulary.** `sources:` became `ingestion_job_definitions:`, `steps:`
became `transform_job_definitions:`, and the `schedule:` field on either one
became a `job_schedules:` row pointing at it by id.

**The file split.** `qanat.yaml` keeps what a person writes and means to keep;
what a tool writes moves to its own file beside it. The boundary is not
tidiness, it is human-written against tool-written, and the two were sharing a
file -- `define_metric` loaded `qanat.yaml`, mutated the model and re-dumped the
whole thing, which cost the project's own header, the note explaining why a row
needs a publication date, the note naming which tables the reading tools open,
the section spacing and the hand-wrapped metric SQL. All of it survived only in
`qanat.yaml.bak`.

**Why a migration rather than an alias.** An alias leaves both vocabularies in
the record, which is the state the rename exists to end -- and a `schedule:`
field quietly read as a schedule row would be a *different* pipeline than the
one on the page, because the row can be joined, disabled and duplicated and the
field could not. So the old shapes are refused at load, and this is the one
thing that reads them.

**It is a verb a person runs**, which is why it is a fifth command on a CLI that
is otherwise four. It runs on a file that cannot be loaded yet, so no tool over
MCP could reach it -- and rewriting somebody's architecture file is not a thing
to do silently on the way past. It prints before it writes.

What it does not do: guess. A `schedule:` becomes a schedule row with an id made
from the definition's, and nothing else moves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Keys that mean the project predates the split of one object into four.
OLD_KEYS = ("sources", "steps")


class MigrateError(Exception):
    pass


@dataclass
class Migration:
    path: Path
    #: What moved, in the order it moved, for printing.
    notes: list[str] = field(default_factory=list)
    #: What stays in `qanat.yaml`. Written only if `apply` is called.
    architecture: dict[str, Any] = field(default_factory=dict)
    #: filename -> the keys that move into it.
    parts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def needed(self) -> bool:
        return bool(self.notes)


def looks_old(raw: dict[str, Any]) -> bool:
    """Does this file need migrating? Either change counts."""
    from qanat.project_io import part_for

    return any(k in raw for k in OLD_KEYS) or any(part_for(k) for k in raw)


def schedule_id_for(definition_id: str) -> str:
    """One schedule per migrated field, named after the definition it came from.

    Deterministic, so running the migration twice would name the same row rather
    than growing a second one.
    """
    return f"{definition_id}_cron"[:60]


def plan_migration(path: str | os.PathLike[str]) -> Migration:
    """What would change. Reads, writes nothing."""
    from qanat.project_io import part_for

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

    renamed: dict[str, Any] = {}
    schedules: list[dict[str, Any]] = list(raw.get("job_schedules") or [])
    for key, new_key, what in (("sources", "ingestion_job_definitions", "ingestion job"),
                               ("steps", "transform_job_definitions", "transform")):
        entries = raw.get(key)
        if entries is None:
            continue
        moved = []
        for entry in entries:
            one = dict(entry or {})
            cron = one.pop("schedule", None)
            # `mode: replace` on an ingestion job deletes every row outside the
            # current selection. The model refuses it, so leaving it here would
            # produce files that still cannot be loaded -- and the honest fix is
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
        renamed[new_key] = moved
        m.notes.append(
            f"{key}: -> {new_key}: ({len(moved)} entr{'y' if len(moved) == 1 else 'ies'})")

    # What the file holds once the renames have been applied in place.
    after: dict[str, Any] = {}
    for key, value in raw.items():
        if key in OLD_KEYS:
            new_key = ("ingestion_job_definitions" if key == "sources"
                       else "transform_job_definitions")
            if new_key in renamed:
                after[new_key] = renamed[new_key]
            continue
        after[key] = value
    if schedules:
        after["job_schedules"] = schedules

    # Then the split: every key a tool owns leaves this file.
    for key, value in after.items():
        name = part_for(key)
        if name is None:
            m.architecture[key] = value
        else:
            m.parts.setdefault(name, {})[key] = value
    for name, held in m.parts.items():
        m.notes.append(f"{', '.join(held)} -> {name}")
    return m


def apply_migration(m: Migration) -> list[Path]:
    """Write the new files, keeping a copy of the old one beside them.

    The backup is not politeness. The round-tripper cannot carry a comment from a
    key that no longer exists, so a note written above `sources:` has nowhere to
    land -- and the one place it survives is the file it was written in.
    """
    if not m.needed:
        return []
    from qanat.project_io import _write, render_project

    root = m.path.parent
    backup = root / "qanat.yaml.before-the-job-model"
    backup.write_text(m.path.read_text())

    written = [backup]
    for name, held in m.parts.items():
        path = root / name
        _write(path, render_project(held, path))
        written.append(path)
    # Last, and in place, so the comments on the keys that stay survive: the
    # round-tripper lays the new values onto the tree parsed from this file.
    _write(m.path, render_project(m.architecture, m.path))
    written.append(m.path)

    old_dir = root / "steps"
    new_dir = root / "transforms"
    if old_dir.is_dir() and not new_dir.exists():
        old_dir.rename(new_dir)
        written.append(new_dir)
    return written


__all__ = ["MigrateError", "Migration", "apply_migration", "looks_old", "plan_migration"]
