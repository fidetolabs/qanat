"""Read a project written the old way, and write it the new way.

Three changes, one command, because a project that needs any of them usually
needs the others.

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

**The metrics.** `metrics:` was a list of formula objects, each with its own
`version:` integer, written in `metrics.yaml`; `corpus.metrics` named the one
table they all landed in. A metric is a `transform_job_definition` writing its
own table now, in a stage declaring `kind: metrics`, and the stage is the
registry -- so the list has nothing left that it alone can hold.

**A formula naming `:as_of` cannot be migrated and is reported rather than
dropped.** That kind took the date as a parameter and stored nothing, so a
screen had to run it -- and with no date to bind it ran over every row, which is
how a screen at 2024-06-15 could return a figure that was not public for five
more months. Converting it means deciding which column carries the date each
value became true, and that is a judgement about the formula's meaning. The
migration says which formulas need it and leaves them in the backup.

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

#: Where a migrated metric's generated script goes.
METRIC_SCRIPTS = "transforms/metrics"


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
    #: path -> body, for the scripts a migrated metric needs. A metric is a
    #: transform now, and a transform runs a file.
    scripts: dict[str, str] = field(default_factory=dict)

    @property
    def needed(self) -> bool:
        return bool(self.notes)


def looks_old(architecture: dict[str, Any],
              whole: dict[str, Any] | None = None) -> bool:
    """Does this project need migrating? Any of the three changes counts.

    **Two dicts, because two of the three tests ask different questions.** The
    vocabulary and the split are about what is in `qanat.yaml` -- a project whose
    jobs already live in `jobs.yaml` has been split, and seeing those keys in a
    merged view would say it had not. The metrics change is about the project as
    a whole, and the metric list is in the one file the split moved it to.
    """
    from qanat.project_io import part_for

    if any(k in architecture for k in OLD_KEYS):
        return True
    if any(part_for(k) for k in architecture):
        return True
    whole = architecture if whole is None else whole
    # A metric list, or a corpus entry naming the one table they all landed in.
    return bool(whole.get("metrics")) or bool((whole.get("corpus") or {}).get("metrics"))


def schedule_id_for(definition_id: str) -> str:
    """One schedule per migrated field, named after the definition it came from.

    Deterministic, so running the migration twice would name the same row rather
    than growing a second one.
    """
    return f"{definition_id}_cron"[:60]


def _metrics_to_transforms(
    raw: dict[str, Any], m: Migration,
) -> tuple[str, list[dict[str, Any]], dict[str, str]]:
    """Turn a `metrics:` list into transform definitions writing their own tables.

    Returns the stage they land in, the definitions, and the scripts to write.

    **A formula naming `:as_of` is reported and not converted.** Deciding which
    column carries the date each value became true is a judgement about what the
    formula means, and guessing it would store numbers under dates they do not
    mean. The old file is kept beside the new ones, so nothing is lost.

    **Only the newest version of each id is carried.** The integer version is
    gone -- a metric's version is its definition's digest now -- so two entries
    at one id would be two definitions writing one table under one name. The
    older formulas stay in the backup, and the values they already wrote stay in
    the store, each naming the execution that wrote it.
    """
    from qanat.metrics import transform_sql

    entries = list(raw.get("metrics") or [])
    stage_from_corpus = str((raw.get("corpus") or {}).get("metrics") or "")
    if not entries and not stage_from_corpus:
        return "", [], {}

    # Where they land. **A stage already declaring `kind: metrics`, or a new one
    # called `metrics`.** Not the stage the old corpus ref named: that ref was
    # `features.metrics`, a *table* inside a features stage, and reusing the
    # stage part of it would land every metric in `features` -- where nothing
    # would recognise it, because the registry is the stage's `kind`. Measured:
    # done that way, a migrated project loaded, checked and ran, and reported no
    # metrics at all.
    stage = ""
    for one in raw.get("stages") or []:
        if (one or {}).get("kind") == "metrics":
            stage = str((one or {}).get("id") or "")
            break
    stage = stage or "metrics"
    if stage_from_corpus:
        m.notes.append(
            f"the rows already in '{stage_from_corpus}' stay where they are. They name "
            f"the executions that wrote them, and a migration is not a reason to unsay "
            f"what somebody was shown -- but a screen reads '{stage}.<id>' now, so "
            f"re-run the definitions to fill the new tables"
        )

    newest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        one = dict(entry or {})
        mid = str(one.get("id") or "")
        if not mid:
            continue
        if mid in newest and int(one.get("version") or 1) <= int(newest[mid].get("version") or 1):
            continue
        newest[mid] = one

    definitions: list[dict[str, Any]] = []
    scripts: dict[str, str] = {}
    for mid, one in newest.items():
        versions = sorted({int(x.get("version") or 1) for x in entries
                           if str(x.get("id") or "") == mid})
        if len(versions) > 1:
            m.notes.append(
                f"metric '{mid}': versions {', '.join(map(str, versions))} -> one "
                f"definition, from version {max(versions)}. A metric's version is its "
                f"definition's digest now, so the older formulas have no numbered slot "
                f"to sit in -- they stay in the backup, and the values they wrote stay "
                f"in the store naming the executions that wrote them"
            )
        sql = str(one.get("sql") or "")
        if ":as_of" in sql:
            m.notes.append(
                f"metric '{mid}': **NOT migrated.** Its formula takes the date as a "
                f"parameter (`:as_of`), which stored nothing -- so a screen had to run "
                f"it, and with no date to bind it ran over every row. Rewrite it to "
                f"select a `date` column carrying the day each value became true, then "
                f"call define_metric. The formula is in the backup beside this file"
            )
            continue
        ref = f"{stage}.{mid}"
        script = f"{METRIC_SCRIPTS}/{mid}.sql"
        scripts[script] = transform_sql(mid, sql)
        options = {k: one[k] for k in ("unit", "description") if one.get(k)}
        entry_out: dict[str, Any] = {
            "id": mid,
            "from": _tables_named(raw, sql),
            "to": [ref],
            "script": script,
            "key": ["subject", "date"],
        }
        if options:
            entry_out["options"] = options
        definitions.append(entry_out)
        m.notes.append(
            f"metric '{mid}': -> transform '{mid}' writing {ref}, script {script}")

    return stage, definitions, scripts


def _tables_named(raw: dict[str, Any], sql: str) -> list[str]:
    """Which of this project's tables a formula reads, by physical name.

    Read off the file rather than off a loaded project, because the point of a
    migration is that the file cannot be loaded yet.
    """
    import re

    refs: list[str] = []
    for key in ("sources", "ingestion_job_definitions",
                "steps", "transform_job_definitions"):
        for entry in raw.get(key) or []:
            refs.extend((entry or {}).get("to") or (entry or {}).get("writes") or [])
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
    return [ref for ref in dict.fromkeys(refs) if ref.replace(".", "__") in words]


def plan_migration(path: str | os.PathLike[str]) -> Migration:
    """What would change. Reads, writes nothing."""
    from qanat.project_io import part_for

    f = Path(path).resolve()
    if f.is_dir():
        f = f / "qanat.yaml"
    if not f.is_file():
        raise MigrateError(f"no qanat.yaml at {f}")
    with f.open() as fh:
        architecture = yaml.safe_load(fh) or {}

    # **The part files beside it count as part of the project.** A project that
    # has already been split holds its metrics in `metrics.yaml` and its jobs in
    # `jobs.yaml`, and reading only the architecture file would have migrated
    # neither -- the metric list was in the one file this never opened.
    from qanat.project_io import PARTS, RETIRED_PARTS

    raw = dict(architecture)
    for name in (*PARTS, *RETIRED_PARTS):
        part = f.parent / name
        if not part.is_file():
            continue
        with part.open() as fh:
            more = yaml.safe_load(fh) or {}
        if isinstance(more, dict):
            raw.update(more)

    m = Migration(path=f)
    if not looks_old(architecture, raw):
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

    # ---- the metrics ---------------------------------------------------------
    metric_stage, metric_transforms, scripts = _metrics_to_transforms(raw, m)
    if metric_transforms:
        renamed.setdefault("transform_job_definitions",
                           list(raw.get("transform_job_definitions") or []))
        renamed["transform_job_definitions"] = [
            *renamed["transform_job_definitions"], *metric_transforms]
    m.scripts = scripts

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
    if metric_transforms:
        after["transform_job_definitions"] = renamed["transform_job_definitions"]
    after.pop("metrics", None)
    if metric_stage:
        stages = list(after.get("stages") or [])
        if not any((x or {}).get("kind") == "metrics" for x in stages):
            # **A stage with the right id and the wrong kind gets its kind
            # fixed, not a twin.** `check` used to permit chaining inside a
            # stage only when `kind == "features"`, so a project wanting a
            # metric to read another metric declared `id: metrics, kind:
            # features` -- a documented workaround. Appending beside it would
            # produce two stages with one id, which is not loadable.
            named = next((x for x in stages if (x or {}).get("id") == metric_stage), None)
            if named is not None:
                was = named.get("kind")
                named["kind"] = "metrics"
                m.notes.append(
                    f"stage '{metric_stage}': kind {was!r} -> 'metrics'. It was named "
                    f"for metrics and typed as something else, which only worked because "
                    f"chaining inside a stage needed `kind: features` -- any stage but "
                    f"raw may chain now, so the kind can say what the stage is"
                )
            else:
                stages.append({
                    "id": metric_stage, "kind": "metrics",
                    "description": "one number per subject per date, screenable"})
                m.notes.append(
                    f"stages: + '{metric_stage}' (kind: metrics). **The stage is the "
                    f"registry** -- every table in it is a metric, so no list is kept "
                    f"beside them"
                )
            after["stages"] = stages
        corpus = dict(after.get("corpus") or {})
        if "metrics" in corpus:
            was = corpus.pop("metrics")
            after["corpus"] = corpus
            m.notes.append(
                f"corpus.metrics: '{was}' -> dropped. One metric is one table now, so a "
                f"single ref cannot name them -- and a shared table makes a metric "
                f"computed from another metric impossible"
            )
        clocks = dict(after.get("time_columns") or {})
        for entry in metric_transforms:
            ref = entry["to"][0]
            clocks.setdefault(ref, "date")
        after["time_columns"] = clocks

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

    # A migrated metric is a transform, and a transform runs a file.
    for rel, body in m.scripts.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        written.append(path)

    # `metrics.yaml` held the list, and the list is gone. Moved aside rather
    # than deleted: a formula this migration refused to convert is in it.
    old_metrics = root / "metrics.yaml"
    if old_metrics.is_file():
        kept = root / "metrics.yaml.before-the-job-model"
        kept.write_text(old_metrics.read_text())
        old_metrics.unlink()
        written.append(kept)
    return written


__all__ = ["MigrateError", "Migration", "apply_migration", "looks_old", "plan_migration"]
