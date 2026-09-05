"""Read and write qanat.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from qanat.models import Project, Source, Stage, Step


def dump_project(project: Project) -> dict[str, Any]:
    """Project -> dict using YAML field names (from/to, project)."""
    data: dict[str, Any] = {
        "project": project.name,
        "store": project.store,
    }
    if project.universes:
        data["universes"] = [
            {"id": u.id, "index": u.index, "symbols": u.symbols} for u in project.universes
        ]
    data["stages"] = [
        {"id": s.id, "kind": s.kind, **({"description": s.description} if s.description else {})}
        for s in project.stages
    ]
    if project.sources:
        data["sources"] = [_dump_source(s) for s in project.sources]
    if project.steps:
        data["steps"] = [_dump_step(st) for st in project.steps]
    if project.retention:
        data["retention"] = dict(project.retention)
    if project.time_columns:
        data["time_columns"] = dict(project.time_columns)
    if project.backtest is not None:
        # Anything the model holds has to be written back, or saving one step from
        # the console quietly deletes the rest of the file.
        data["backtest"] = project.backtest.model_dump()
    return data


def _dump_source(s: Source) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": s.id,
        "to": list(s.writes),
        "connector": s.connector,
    }
    if s.schedule:
        row["schedule"] = s.schedule
    if s.key:
        row["key"] = list(s.key)
    if s.mode != "append":
        row["mode"] = s.mode
    if s.options:
        row["options"] = s.options
    return row


def _dump_step(st: Step) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": st.id,
        "from": list(st.reads),
        "to": list(st.writes),
        "script": st.script,
    }
    if st.schedule:
        row["schedule"] = st.schedule
    if st.when:
        row["when"] = list(st.when)
    if st.universe:
        row["universe"] = st.universe
    if st.rebalance:
        row["rebalance"] = st.rebalance
    if st.decay:
        row["decay"] = st.decay
    if st.options:
        row["options"] = st.options
    return row


def save_project(project: Project, root: Path) -> Path:
    """Write qanat.yaml. Returns the file path."""
    path = root / "qanat.yaml"
    data = dump_project(project)
    text = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    path.write_text(text)
    return path


def project_from_dict(raw: dict[str, Any]) -> Project:
    return Project.model_validate(raw)


def mutate_stage(project: Project, stage: Stage, *, insert_before: str | None = None) -> None:
    """Add or replace a stage. `insert_before` places a new stage before that id."""
    for i, s in enumerate(project.stages):
        if s.id == stage.id:
            project.stages[i] = stage
            return
    if insert_before:
        idx = next((i for i, s in enumerate(project.stages) if s.id == insert_before), -1)
        if idx < 0:
            raise ValueError(f"unknown stage '{insert_before}'")
        project.stages.insert(idx, stage)
    else:
        project.stages.append(stage)


def remove_stage(project: Project, stage_id: str) -> None:
    for s in project.sources:
        if s.stage == stage_id:
            raise ValueError(f"source '{s.id}' writes to stage '{stage_id}'")
    for st in project.steps:
        for ref in [*st.reads, *st.writes]:
            if ref.split(".")[0] == stage_id:
                raise ValueError(f"step '{st.id}' still uses stage '{stage_id}'")
    project.stages = [s for s in project.stages if s.id != stage_id]
    project.retention = {k: v for k, v in project.retention.items() if not k.startswith(f"{stage_id}.")}


def upsert_source(project: Project, source: Source) -> None:
    project.sources = [s for s in project.sources if s.id != source.id] + [source]


def remove_source(project: Project, source_id: str) -> None:
    project.sources = [s for s in project.sources if s.id != source_id]


def upsert_step(project: Project, step: Step) -> None:
    project.steps = [s for s in project.steps if s.id != step.id] + [step]


def remove_step(project: Project, step_id: str) -> None:
    project.steps = [s for s in project.steps if s.id != step_id]
