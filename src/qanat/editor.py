"""Project mutations: one function per edit, each validated before it lands.

The write tools in `mcp` are the callers. Nothing here writes to the store -- an
edit changes `qanat.yaml` and, for a new transform, the script file beside it.
"""

from __future__ import annotations

from pathlib import Path

from qanat.models import (
    IngestionJobDefinition,
    JobSchedule,
    Metric,
    Project,
    TransformJobDefinition,
)
from qanat.project import validate
from qanat.project_io import (
    remove_ingestion,
    remove_schedule,
    remove_transform,
    save_project,
    upsert_ingestion,
    upsert_schedule,
    upsert_transform,
)


class EditorError(Exception):
    pass


STUB_SQL = """\
-- New transform. Reads the upstream tables declared in `from:`.
SELECT *
FROM {from_table}
LIMIT 0
"""

STUB_PY = '''\
"""New transform. Edit run(ctx) and return a DataFrame (or dict of them)."""


def run(ctx):
    raise NotImplementedError("edit this transform")
'''


def _edit(project: Project, root: Path, change) -> list[str]:
    """Apply a change to a copy, and keep it only if the whole project still holds.

    Editing in place and validating afterwards meant a rejected edit stayed in
    memory: every later read then reported errors about a definition not in the
    file, and every later edit failed against it until the process was restarted.
    One bad field took the whole editor down.
    """
    draft = project.model_copy(deep=True)
    change(draft)
    rep = validate(draft, root)
    if not rep.ok:
        raise EditorError("; ".join(rep.errors))
    save_project(draft, root)
    for name in type(draft).model_fields:
        setattr(project, name, getattr(draft, name))
    return rep.warnings


def save_ingestion(project: Project, root: Path, raw: dict) -> list[str]:
    definition = IngestionJobDefinition.model_validate(raw)
    return _edit(project, root, lambda d: upsert_ingestion(d, definition))


def save_transform(project: Project, root: Path, raw: dict,
                   create_script: bool = True) -> list[str]:
    definition = TransformJobDefinition.model_validate(raw)
    script = root / definition.script
    # The stub used to be written before anything was checked, so a request the API
    # rejected with a 400 still left a file on disk -- at whatever path was asked
    # for, including one outside the project. Validate first, write second.
    stub = None
    if create_script and not script.is_file():
        if script.suffix == ".sql":
            from_table = (definition.reads[0].replace(".", "__")
                          if definition.reads else "raw__bars")
            stub = STUB_SQL.format(from_table=from_table)
        else:
            stub = STUB_PY
        _check_inside(root, definition)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(stub)
    try:
        return _edit(project, root, lambda d: upsert_transform(d, definition))
    except Exception:
        if stub is not None and script.is_file() and script.read_text() == stub:
            script.unlink()          # a rejected edit leaves nothing behind
        raise


def save_schedule(project: Project, root: Path, raw: dict) -> list[str]:
    """Add or replace one cadence over one definition.

    Several may point at the same definition, which is the whole reason a
    schedule is a row. Replacing is matched on the schedule's own id, so saving
    a weekly one does not touch the daily one beside it.
    """
    schedule = JobSchedule.model_validate(raw)
    return _edit(project, root, lambda d: upsert_schedule(d, schedule))


def delete_schedule(project: Project, root: Path, schedule_id: str) -> list[str]:
    if project.schedule(schedule_id) is None:
        raise EditorError(
            f"no schedule '{schedule_id}'. Schedules: "
            f"{', '.join(s.id for s in project.job_schedules) or '(none)'}"
        )
    return _edit(project, root, lambda d: remove_schedule(d, schedule_id))


def _check_inside(root: Path, definition: TransformJobDefinition) -> None:
    """A transform runs the file it names, so that file has to be in the project."""
    try:
        inside = (root / definition.script).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise EditorError(
            f"transform '{definition.id}': script '{definition.script}' is outside the "
            "project. A transform runs the file it names, so it has to be a path under "
            "the project directory"
        )


def save_universe(root: Path, uid: str, symbols: list[str],
                  file: str | None = None) -> Path:
    """Write a list of symbols as a csv a connector can name.

    **The file is the whole of it.** A connector's `from_universe` option
    resolves `./universes/<id>.csv` or any path under the project, and reads the
    symbols out of it -- so naming a basket of two hundred companies once beats
    pasting them into every definition that polls them.

    It used to also write a `universes:` entry in the project, read by
    `ctx.universe()` inside a transform script. That was a basket held by one
    stage of the pipeline, which is the shape the `weights` and `pnl` stages
    had, and they left the platform in 0.5.0. Nothing read the entry after that: `screen` takes the
    list with the call and refuses a named one in as many words.

    This stays a tool because **an agent over MCP cannot write a file**, and the
    error it used to get named a fix nobody offered.
    """
    rel = file or f"./universes/{uid}.csv"
    path = root / rel
    if not symbols:
        raise EditorError("a universe needs at least one symbol")
    if not path.resolve().is_relative_to(root.resolve()):
        raise EditorError(f"symbols file '{rel}' is outside the project")
    path.parent.mkdir(parents=True, exist_ok=True)
    # `from` and `to` stay empty, and the caller is told: a list with no join
    # and leave dates is today's membership applied to the past, which flatters
    # every number built on it.
    path.write_text("symbol,name,sector,from,to\n" +
                    "".join(f"{s},,,,\n" for s in symbols))
    return path


def delete_transform(project: Project, root: Path, definition_id: str) -> list[str]:
    return _edit(project, root, lambda d: remove_transform(d, definition_id))


def save_metric(project: Project, root: Path, metric: Metric) -> list[str]:
    """Add a definition, or replace the one at the same id and version.

    Matched on both halves, because a bumped version is a new definition stored
    beside the old rather than over it. Written in version order so the file
    reads the way the history ran.
    """
    def change(d: Project) -> None:
        kept = [m for m in d.metrics
                if not (m.id == metric.id and m.version == metric.version)]
        d.metrics = sorted([*kept, metric], key=lambda m: (m.id, m.version))
    return _edit(project, root, change)


def delete_metric(project: Project, root: Path, mid: str,
                  version: int | None = None) -> list[str]:
    """Drop a definition, or one version of it. `version` None means all of them."""
    def change(d: Project) -> None:
        d.metrics = [m for m in d.metrics
                     if m.id != mid or (version is not None and m.version != int(version))]
    return _edit(project, root, change)


def delete_ingestion(project: Project, root: Path, definition_id: str) -> list[str]:
    """Stop an ingestion job. The rows it landed stay -- data, not wiring."""
    if not any(d.id == definition_id for d in project.ingestion_job_definitions):
        raise EditorError(
            f"no ingestion job '{definition_id}'. Known: "
            f"{', '.join(d.id for d in project.ingestion_job_definitions) or '(none)'}"
        )
    return _edit(project, root, lambda d: remove_ingestion(d, definition_id))


