"""Project mutations used by the console API."""

from __future__ import annotations

from pathlib import Path

from qanat.models import Project, Source, Stage, Step
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


def replace_project(root: Path, raw: dict) -> tuple[Project, list[str]]:
    project = project_from_dict(raw)
    warnings = apply_and_save(root, project)
    return project, warnings


def set_store(project: Project, root: Path, store: str) -> list[str]:
    project.store = store
    return apply_and_save(root, project)


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
    mutate_stage(project, Stage(id=stage_id, kind=kind, description=description), insert_before=before)
    return apply_and_save(root, project)


def delete_stage(project: Project, root: Path, stage_id: str) -> list[str]:
    lay = project.stage(stage_id)
    if lay and lay.kind == "raw":
        raise EditorError("the raw stage cannot be removed")
    if lay and lay.kind == "weights":
        raise EditorError("the weights stage cannot be removed")
    remove_stage(project, stage_id)
    return apply_and_save(root, project)


def save_source(project: Project, root: Path, raw: dict) -> list[str]:
    upsert_source(project, Source.model_validate(raw))
    return apply_and_save(root, project)


def delete_source(project: Project, root: Path, source_id: str) -> list[str]:
    remove_source(project, source_id)
    return apply_and_save(root, project)


def save_step(project: Project, root: Path, raw: dict, create_script: bool = True) -> list[str]:
    step = Step.model_validate(raw)
    script = root / step.script
    if create_script and not script.is_file():
        script.parent.mkdir(parents=True, exist_ok=True)
        if script.suffix == ".sql":
            from_table = step.reads[0].replace(".", "__") if step.reads else "raw__bars"
            script.write_text(STUB_SQL.format(from_table=from_table))
        else:
            script.write_text(STUB_PY)
    upsert_step(project, step)
    return apply_and_save(root, project)


def delete_step(project: Project, root: Path, step_id: str) -> list[str]:
    remove_step(project, step_id)
    return apply_and_save(root, project)


def set_retention(project: Project, root: Path, retention: dict[str, str]) -> list[str]:
    from qanat.retention import parse_duration

    for ref, policy in retention.items():
        if ref.count(".") != 1:
            raise EditorError(f"retention key must be stage.table, got {ref!r}")
        parse_duration(policy)
    project.retention = dict(retention)
    return apply_and_save(root, project)


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
