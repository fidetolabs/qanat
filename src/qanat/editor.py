"""Project mutations used by the console API."""

from __future__ import annotations

from pathlib import Path

from qanat.models import Project, Source, Stage, Step, Universe
from qanat.project import validate
from qanat.project_io import (
    mutate_stage,
    project_from_dict,
    remove_source,
    remove_stage,
    remove_step,
    save_project,
    upsert_source,
    upsert_step,
)


class EditorError(Exception):
    pass


STUB_SQL = """\
-- New step. Reads from upstream tables declared in `from:`.
SELECT *
FROM {from_table}
LIMIT 0
"""

STUB_PY = '''\
"""New step. Edit run(ctx) and return a DataFrame (or dict of DataFrames)."""


def run(ctx):
    raise NotImplementedError("edit this step")
'''


def load_mutable(root: Path) -> tuple[Project, Path]:
    from qanat.project import load

    return load(root)


def apply_and_save(root: Path, project: Project) -> list[str]:
    rep = validate(project, root)
    if not rep.ok:
        raise EditorError("; ".join(rep.errors))
    save_project(project, root)
    return rep.warnings


def _edit(project: Project, root: Path, change) -> list[str]:
    """Apply a change to a copy, and keep it only if the whole project still holds.

    Editing in place and validating afterwards meant a rejected edit stayed in
    memory: the console then reported errors about a step that is not in the file,
    and every later edit failed against it until the server was restarted. One bad
    form field took the editor down.
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


def replace_project(root: Path, raw: dict) -> tuple[Project, list[str]]:
    project = project_from_dict(raw)
    warnings = apply_and_save(root, project)
    return project, warnings


def set_store(project: Project, root: Path, store: str) -> list[str]:
    def change(d: Project) -> None:
        d.store = store
    return _edit(project, root, change)


def add_stage(
    project: Project,
    root: Path,
    stage_id: str,
    kind: str = "features",
    description: str = "",
    before: str | None = None,
) -> list[str]:
    if before is None:
        weights = project.weights_stage
        before = weights.id if weights else None
    stage = Stage(id=stage_id, kind=kind, description=description)
    return _edit(project, root, lambda d: mutate_stage(d, stage, insert_before=before))


def delete_stage(project: Project, root: Path, stage_id: str) -> list[str]:
    lay = project.stage(stage_id)
    if lay and lay.kind == "raw":
        raise EditorError("the raw stage cannot be removed")
    if lay and lay.kind == "weights":
        raise EditorError("the weights stage cannot be removed")
    return _edit(project, root, lambda d: remove_stage(d, stage_id))


def save_source(project: Project, root: Path, raw: dict) -> list[str]:
    source = Source.model_validate(raw)
    return _edit(project, root, lambda d: upsert_source(d, source))


def delete_source(project: Project, root: Path, source_id: str) -> list[str]:
    return _edit(project, root, lambda d: remove_source(d, source_id))


def save_step(project: Project, root: Path, raw: dict, create_script: bool = True) -> list[str]:
    step = Step.model_validate(raw)
    script = root / step.script
    # The stub used to be written before anything was checked, so a request the API
    # rejected with a 400 still left a file on disk -- at whatever path was asked
    # for, including one outside the project. Validate first, write second.
    stub = None
    if create_script and not script.is_file():
        if script.suffix == ".sql":
            from_table = step.reads[0].replace(".", "__") if step.reads else "raw__bars"
            stub = STUB_SQL.format(from_table=from_table)
        else:
            stub = STUB_PY
        _check_inside(root, step)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(stub)
    try:
        return _edit(project, root, lambda d: upsert_step(d, step))
    except Exception:
        if stub is not None and script.is_file() and script.read_text() == stub:
            script.unlink()          # a rejected edit leaves nothing behind
        raise


def _check_inside(root: Path, step: Step) -> None:
    """A step runs the file it names, so that file has to be inside the project."""
    try:
        inside = (root / step.script).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        raise EditorError(
            f"step '{step.id}': script '{step.script}' is outside the project. A step runs "
            "the file it names, so it has to be a path under the project directory"
        )


def save_universe(project: Project, root: Path, raw: dict) -> list[str]:
    """Add or replace a universe, writing its symbol file if symbols were given.

    Every shelf alpha holds itself to one, and a project that declares none was a
    dead end: `use_alpha` refused, and told whoever asked to add one to
    `qanat.yaml` by hand -- which an agent reaching this project over MCP cannot
    do. The error named a fix that no tool offered.
    """
    symbols = raw.pop("symbols_list", None)
    universe = Universe.model_validate(raw)
    path = root / universe.symbols
    if symbols is not None:
        if not symbols:
            raise EditorError("a universe needs at least one symbol")
        if not path.resolve().is_relative_to(root.resolve()):
            raise EditorError(
                f"symbols file '{universe.symbols}' is outside the project"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        # `from` and `to` stay empty: a list with no join and leave dates is
        # today's membership applied to the past, and `validate` says so out loud.
        path.write_text("symbol,name,sector,from,to\n" +
                        "".join(f"{s},,,,\n" for s in symbols))
    elif not path.is_file():
        raise EditorError(
            f"no symbols file at {universe.symbols}. Pass `symbols_list` and one "
            "will be written, or put the csv there first"
        )

    def change(d: Project) -> None:
        d.universes = [u for u in d.universes if u.id != universe.id] + [universe]
    return _edit(project, root, change)


def delete_universe(project: Project, root: Path, universe_id: str) -> list[str]:
    used = [st.id for st in project.steps if st.universe == universe_id]
    if used:
        raise EditorError(
            f"universe '{universe_id}' is still held by {', '.join(used)}. "
            "Point those steps elsewhere first"
        )

    def change(d: Project) -> None:
        d.universes = [u for u in d.universes if u.id != universe_id]
    return _edit(project, root, change)


def delete_step(project: Project, root: Path, step_id: str) -> list[str]:
    return _edit(project, root, lambda d: remove_step(d, step_id))


def set_retention(project: Project, root: Path, retention: dict[str, str]) -> list[str]:
    from qanat.retention import parse_duration

    for ref, policy in retention.items():
        if ref.count(".") != 1:
            raise EditorError(f"retention key must be stage.table, got {ref!r}")
        parse_duration(policy)
    def change(d: Project) -> None:
        d.retention = dict(retention)
    return _edit(project, root, change)


def drop_table(store, project: Project, root: Path, ref: str, *, force: bool = False) -> int:
    from qanat.plan import plan

    producers = project.producers()
    pl = plan(project, root, store)
    orphan = ref in {c.target for c in pl.orphans}
    if ref in producers and not force:
        raise EditorError(f"{ref} is still produced by '{producers[ref]}'. Remove the job first")
    if not orphan and ref not in producers and not force:
        raise EditorError(f"{ref} is not in the project")
    rows = store.drop(ref)
    project.retention.pop(ref, None)
    if ref in producers:
        save_project(project, root)
    store.event("warn", "editor", f"dropped {ref} ({rows:,} rows)")
    return rows
