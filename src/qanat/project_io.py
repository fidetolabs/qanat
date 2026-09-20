"""Read and write qanat.yaml."""

from __future__ import annotations

import os
import threading
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
    if project.job_timeout:
        data["job_timeout"] = project.job_timeout
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
    if s.timeout:
        row["timeout"] = s.timeout
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
    if st.timeout:
        row["timeout"] = st.timeout
    if st.rebalance:
        row["rebalance"] = st.rebalance
    if st.decay:
        row["decay"] = st.decay
    if st.options:
        row["options"] = st.options
    return row


#: One writer at a time. The console edits from a request thread and the scheduler
#: stamps `live_from` from its own, and a plain write_text let them interleave: two
#: in four hundred reads got a half-written file, and one writer's change was lost.
_WRITE = threading.RLock()


def _round_tripper() -> Any:
    """ruamel's round-trip YAML, or None if it is not installed.

    Optional on purpose. An install that predates this dependency should keep
    saving projects rather than fail to import, and what it loses by not having it
    is comments -- which is exactly where this file was before.
    """
    try:
        from ruamel.yaml import YAML
    except ImportError:
        return None
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096          # never reflow a long line into a folded one
    y.indent(mapping=2, sequence=2, offset=0)
    return y


def _merge(old: Any, new: Any) -> Any:
    """The new value, laid onto the old node so its comments survive.

    Comments live on the node, not in the model, so a dump of the model alone
    cannot carry them. Writing the new values *into* the tree that was parsed from
    the file keeps every comment attached to a key or a block that is still there.

    Lists are matched on `id` -- a step, a stage, a source -- so editing one step
    leaves the comments on its neighbours alone. Anything without an `id` is
    replaced wholesale, which is right for the scalar lists in this file.
    """
    if isinstance(new, dict) and hasattr(old, "keys"):
        for key in [k for k in old if k not in new]:
            del old[key]
        for key, value in new.items():
            old[key] = _merge(old[key], value) if key in old else value
        return old

    if isinstance(new, list) and isinstance(old, list):
        kept = {}
        for item in old:
            if isinstance(item, dict) and "id" in item:
                kept[item["id"]] = item
        if not kept:
            return new
        out = []
        for item in new:
            prior = kept.get(item.get("id")) if isinstance(item, dict) else None
            out.append(_merge(prior, item) if prior is not None else item)
        # Mutate in place: a fresh list would drop the comments ruamel stores
        # against the sequence itself, which is where a note above a step lives.
        old[:] = out
        return old

    return new


def render_project(data: dict[str, Any], path: Path) -> str:
    """The project as YAML, keeping whatever comments the file already had.

    `yaml.safe_dump` cannot round-trip a comment, so the first edit an agent made
    stripped every one of them -- including the twelve-line header on
    `examples/fx-bundled` explaining what the dataset is and where it came from.
    Somebody writes those to be read later, and a tool that deletes them on its
    way past is a tool you cannot leave alone with your file.
    """
    plain = yaml.safe_dump(data, sort_keys=False, default_flow_style=False,
                           allow_unicode=True)
    y = _round_tripper()
    if y is None or not path.is_file():
        return plain
    import io

    try:
        with path.open() as fh:
            old = y.load(fh)
        if old is None:
            return plain
        buf = io.StringIO()
        y.dump(_merge(old, data), buf)
        text = buf.getvalue()
    except Exception:  # noqa: BLE001 -- a file we cannot round-trip still has to save
        return plain
    # Never hand back something that will not parse as the project it came from.
    try:
        if yaml.safe_load(text) != data:
            return plain
    except yaml.YAMLError:
        return plain
    return text


def save_project(project: Project, root: Path) -> Path:
    """Write qanat.yaml, atomically, keeping the last good copy.

    `write_text` truncates and then writes, so anything reading in between sees a
    torn file -- `state.reload()` after an edit, `qanat check` in another terminal,
    an agent over MCP. Writing beside it and renaming is atomic on every platform
    this runs on, and costs nothing.
    """
    path = root / "qanat.yaml"
    data = dump_project(project)
    text = render_project(data, path)
    with _WRITE:
        if path.is_file():
            try:
                (root / "qanat.yaml.bak").write_text(path.read_text())
            except OSError:  # a backup we cannot write is not a reason to block the save
                pass
        tmp = path.with_suffix(".yaml.tmp")
        with tmp.open("w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
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
