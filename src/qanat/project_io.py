"""Read a project, and write back the parts a tool owns.

**`qanat.yaml` holds what a person writes and means to keep** -- the project
name, the store, the stages, the tables and the graph between them,
`time_columns`, `corpus`, `returns`, `retention`. Nothing here writes it.

**The parts a tool writes live in their own files beside it.** The boundary is
not tidiness, it is human-written against tool-written, and the two were sharing
a file: `define_metric` loaded `qanat.yaml`, mutated the model and re-dumped the
whole thing. What a re-dump destroyed, measured on the `check` project, was the
note explaining why a row needs a publication date, the note naming which tables
the reading tools open, the section spacing, and the hand-wrapped metric SQL --
including the project's own header saying what the project was for. All of it
survived only in `qanat.yaml.bak`.

So an authoring verb has no write path to `qanat.yaml` at all. `save_project`
refuses to write it, and a field belonging to it that has changed in memory is a
bug rather than a save.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import yaml

from qanat.models import (
    IngestionJobDefinition,
    JobSchedule,
    Metric,
    Project,
    Stage,
    TransformJobDefinition,
)

#: Which file each tool-written key lives in, and in which order inside it.
#: **Comment preservation is attempted and not promised**: these are written by
#: tools, and a key a tool removed has nowhere to keep a note that sat above it.
PARTS: dict[str, tuple[str, ...]] = {
    "jobs.yaml": ("ingestion_job_definitions", "transform_job_definitions",
                  "job_schedules"),
    "metrics.yaml": ("metrics",),
}

#: What `qanat.yaml` holds, and the only keys read out of it.
ARCHITECTURE: tuple[str, ...] = (
    "project", "store", "stages", "time_columns", "corpus", "returns",
    "retention", "job_timeout",
)


def part_for(key: str) -> str | None:
    """Which file owns this key, or None if `qanat.yaml` does."""
    for name, keys in PARTS.items():
        if key in keys:
            return name
    return None


def dump_project(project: Project) -> dict[str, Any]:
    """Project -> dict using YAML field names (from/to, project).

    The whole project in one dict, which is what `check` and the migration want.
    `save_project` splits it across the files that own each key.
    """
    data: dict[str, Any] = {
        "project": project.name,
        "store": project.store,
    }
    data["stages"] = [
        {"id": s.id, "kind": s.kind, **({"description": s.description} if s.description else {})}
        for s in project.stages
    ]
    if project.ingestion_job_definitions:
        data["ingestion_job_definitions"] = [
            _dump_ingestion(d) for d in project.ingestion_job_definitions]
    if project.transform_job_definitions:
        data["transform_job_definitions"] = [
            _dump_transform(d) for d in project.transform_job_definitions]
    if project.job_schedules:
        data["job_schedules"] = [_dump_schedule(s) for s in project.job_schedules]
    if project.metrics:
        data["metrics"] = [_dump_metric(m) for m in project.metrics]
    if project.retention:
        data["retention"] = dict(project.retention)
    if project.time_columns:
        data["time_columns"] = dict(project.time_columns)
    if project.job_timeout:
        data["job_timeout"] = project.job_timeout
    if project.returns is not None:
        data["returns"] = project.returns.model_dump()
    if project.corpus is not None:
        data["corpus"] = project.corpus.model_dump()
    # Anything the model holds has to be written back, or saving one definition
    # quietly deletes the rest of the file. That comment was here and three fields
    # were missing from the dict under it: `metrics`, `returns` and `corpus`. The
    # round-tripper deletes any key the new dict does not carry, so one
    # definition save on a filings project removed every metric definition, the
    # price table a screen reads and the three tables the reading tools read --
    # and the dump then matched the file it had just emptied, so the safety
    # check passed. `test_every_field_the_model_holds_is_written_back` is what
    # stops the next field being added to the model and not to this function.
    return data


def _dump_ingestion(d: IngestionJobDefinition) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": d.id,
        "to": list(d.writes),
        "connector": d.connector,
    }
    if d.key:
        row["key"] = list(d.key)
    if d.timeout:
        row["timeout"] = d.timeout
    if d.options:
        row["options"] = d.options
    return row


def _dump_transform(d: TransformJobDefinition) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": d.id,
        "from": list(d.reads),
        "to": list(d.writes),
        "script": d.script,
    }
    if d.key:
        row["key"] = list(d.key)
    if d.when:
        row["when"] = list(d.when)
    if d.timeout:
        row["timeout"] = d.timeout
    if d.options:
        row["options"] = d.options
    return row


def _dump_schedule(s: JobSchedule) -> dict[str, Any]:
    row: dict[str, Any] = {"id": s.id, "definition": s.definition, "cron": s.cron}
    if not s.enabled:
        row["enabled"] = s.enabled
    return row


def _dump_metric(m: Metric) -> dict[str, Any]:
    row: dict[str, Any] = {"id": m.id, "version": m.version}
    if m.unit:
        row["unit"] = m.unit
    if m.description:
        row["description"] = m.description
    row["sql"] = m.sql
    # Where the definition came from: shipped with the product, or written by a
    # caller. Only written when it is not the default, so an older file that
    # says nothing keeps saying nothing.
    if m.origin != "defined":
        row["origin"] = m.origin
    return row


#: One writer at a time. An agent edits from a request thread and the scheduler
#: writes from its own thread, and a plain write_text let them interleave: two
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

    Lists are matched on `id` -- a definition, a stage, a schedule -- so editing
    one leaves the comments on its neighbours alone. Anything without an `id` is
    replaced wholesale, which is right for the scalar lists in this file.
    """
    if isinstance(new, dict) and hasattr(old, "keys"):
        for key in [k for k in old if k not in new]:
            del old[key]
        order = list(new)
        for i, (key, value) in enumerate(new.items()):
            if key not in old:
                old[key] = value
                continue
            # **A block of comment between two keys is the next key's, not the
            # previous value's** -- but ruamel stores it on the tail of the
            # previous value, wherever that tail happens to end. So a list that
            # grows leaves the block stranded in the middle of itself, where it
            # now introduces whatever was appended. Measured on the `check`
            # project: one `define_metric` put the paragraph explaining
            # `job_schedules:` above a generated transform. **A note that moved
            # is worse than a note that went -- it says something false rather
            # than nothing.**
            after = next((k for k in order[i + 1:] if k in old), None)
            block = _detach_tail_block(old[key]) if isinstance(value, list) else None
            old[key] = _merge(old[key], value)
            if block and after is not None:
                _reintroduce(old, after, block)
            # **A block with no following key is dropped.** It was at the end of
            # the file, and there is no slot after a sequence that ruamel will
            # render -- the one that looks like it (index 0 of the last item) is
            # a *pre*-item comment and lands above the entry instead. So the
            # choice is between losing it and leaving it stranded mid-list, and
            # a note that moved says something false rather than nothing. This
            # is the edge the "attempted, not promised" line is about.
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
        # against the sequence itself, which is where a note above a definition
        # lives.
        old[:] = out
        return old

    return new


def _detach_tail_block(node) -> str | None:
    """Take the comment block that follows a node off wherever ruamel put it.

    Which is one of three places, depending on how the node happens to end: the
    last key of a mapping (slot 2), the last index of a sequence (slot 0), or
    the same again one level down. So this walks the tail rather than guessing.

    The token holds whatever was emitted after the node's own content: an inline
    note on the same line, a block of full-line comment, or both. **The split is
    the first newline**, not the first blank line -- a block that precedes a
    sequence item arrives with no blank line in front of it, which is exactly the
    shape the `check` project produced and the shape a blank-line test missed.
    What is before the newline is the node's own note and stays; what is after
    is the block and is returned.
    """
    for _ in range(16):
        ca = getattr(node, "ca", None)
        if ca is None:
            return None
        if hasattr(node, "keys"):
            keys = list(node.keys())
            if not keys:
                return None
            at = keys[-1]
        elif isinstance(node, list):
            if not len(node):
                return None
            at = len(node) - 1
        else:
            return None
        for i, token in enumerate(ca.items.get(at) or []):
            value = getattr(token, "value", None)
            if not value:
                continue
            mine, _, block = value.partition("\n")
            if "#" not in block:
                continue
            token.value = (mine + "\n") if mine.strip() else ""
            if not token.value:
                ca.items[at][i] = None
            return block
        node = node[at]
    return None


def _reintroduce(parent, next_key, block: str) -> None:
    """Put a block back as the pre-comment of the key that follows it.

    **Which is where it belonged all along.** Stored against the key it
    introduces, it cannot be stranded by the value above it growing -- and that
    is the whole failure this replaces.
    """
    from ruamel.yaml.error import CommentMark
    from ruamel.yaml.tokens import CommentToken

    mark = CommentMark(0)
    # One token per line, the way ruamel builds them, with a blank line in front
    # so the block still reads as a block. An empty line inside it is a bare
    # newline, not a `#` with nothing after it.
    tokens = [CommentToken("\n", mark, None)]
    for line in block.strip("\n").splitlines():
        tokens.append(CommentToken(
            "\n" if not line.strip() else
            (line if line.lstrip().startswith("#") else "# " + line) + "\n",
            mark, None))
    slot = parent.ca.items.setdefault(next_key, [None, None, None, None])
    slot[1] = (slot[1] or []) + tokens


def render_project(data: dict[str, Any], path: Path) -> str:
    """The project as YAML, keeping whatever comments the file already had.

    `yaml.safe_dump` cannot round-trip a comment, so the first edit an agent made
    stripped every one of them -- including the header on a bundled example
    explaining what the dataset is and where it came from.
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


def _write(path: Path, text: str) -> None:
    """Atomically, because `write_text` truncates and then writes.

    Anything reading in between sees a torn file -- `state.reload()` after an
    edit, `qanat check` in another terminal, an agent over MCP. Writing beside it
    and renaming is atomic on every platform this runs on, and costs nothing.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_project(project: Project, root: Path) -> list[Path]:
    """Write the tool-owned part files. **Never `qanat.yaml`.**

    A part with nothing in it is removed rather than left holding an empty key,
    so a project with no schedules does not carry a file saying `job_schedules:`
    and nothing under it.
    """
    whole = dump_project(project)
    written: list[Path] = []
    with _WRITE:
        for name, keys in PARTS.items():
            path = root / name
            mine = {k: whole[k] for k in keys if k in whole}
            if not mine:
                if path.is_file():
                    path.unlink()
                continue
            _write(path, render_project(mine, path))
            written.append(path)
    return written


def architecture_of(project: Project) -> dict[str, Any]:
    """Only the keys `qanat.yaml` owns. For the migration, which rewrites it once."""
    whole = dump_project(project)
    return {k: v for k, v in whole.items() if k in ARCHITECTURE}


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
    for s in project.ingestion_job_definitions:
        if s.stage == stage_id:
            raise ValueError(f"ingestion job '{s.id}' writes to stage '{stage_id}'")
    for st in project.transform_job_definitions:
        for ref in [*st.reads, *st.writes]:
            if ref.split(".")[0] == stage_id:
                raise ValueError(f"transform '{st.id}' still uses stage '{stage_id}'")
    project.stages = [s for s in project.stages if s.id != stage_id]
    project.retention = {k: v for k, v in project.retention.items() if not k.startswith(f"{stage_id}.")}


def upsert_ingestion(project: Project, definition: IngestionJobDefinition) -> None:
    project.ingestion_job_definitions = [
        d for d in project.ingestion_job_definitions if d.id != definition.id
    ] + [definition]


def remove_ingestion(project: Project, definition_id: str) -> None:
    """Drop the definition. Its schedules go with it -- a cadence over a
    definition that is not there fires nothing and reads as live."""
    project.ingestion_job_definitions = [
        d for d in project.ingestion_job_definitions if d.id != definition_id]
    project.job_schedules = [
        s for s in project.job_schedules if s.definition != definition_id]


def upsert_transform(project: Project, definition: TransformJobDefinition) -> None:
    project.transform_job_definitions = [
        d for d in project.transform_job_definitions if d.id != definition.id
    ] + [definition]


def remove_transform(project: Project, definition_id: str) -> None:
    project.transform_job_definitions = [
        d for d in project.transform_job_definitions if d.id != definition_id]
    project.job_schedules = [
        s for s in project.job_schedules if s.definition != definition_id]


def upsert_schedule(project: Project, schedule: JobSchedule) -> None:
    project.job_schedules = [
        s for s in project.job_schedules if s.id != schedule.id] + [schedule]


def remove_schedule(project: Project, schedule_id: str) -> None:
    project.job_schedules = [s for s in project.job_schedules if s.id != schedule_id]
