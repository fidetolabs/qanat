"""Defining a metric, which is the object this platform is named after.

`list_metrics` read them and nothing wrote one. There was no CLI verb either, so
the only object named in the product's own sentence was the only object a caller
could not create -- a metric was hand-written YAML with SQL in it, in a file an
agent otherwise owns, and a scaffolded project shipped none. The product's one
verb therefore failed on a new project:

    ScreenError: no metric called pe. This project has: (none defined)

**The verb is `define`, not `save`.** `save` says *keep this for me*, and there is
no *me* here: a scope is a capability boundary and not an identity one, so this
server cannot tell two callers apart. What it writes is a **definition**, which is
also what the field is called.

## Who owns a definition

**The operator, not the caller.** A formula is arithmetic -- `price / diluted EPS`
is the same number for everybody -- so there is no such thing as a private metric
definition. Whoever runs this Qanat defines its metrics and every reader of it
shares them. That is why having no identity costs nothing here, and it is why
`define_metric` sits above the surface a hosted reader reaches: it is an operator
tool.

**The threshold is the other half and it never enters here.** The number somebody
puts next to a formula is the judgement, and judgement belongs to whoever made it.
It arrives with a screen call and is never stored.

## The version is load-bearing and it is yours to bump

An answer somebody kept points at a definition version. Editing a formula in
place silently changes what that old answer meant, so a changed formula is a new
version written **alongside** the old one, and both still resolve. Nothing here
ever overwrites a version with different SQL without being told the version is
the same.

## A formula that cannot run is not stored

Validated by executing it, against this project, with a date bound into
`:as_of` -- through the same code path a screen uses, so a definition that
validates is a definition that runs. The database's own message comes back
rather than a definition that fails later, on somebody else's call.

The one thing that is not a refusal is a table the project declares and has not
written yet. A metric defined before the first run is the normal order on a
fresh project, and refusing it would mean the scaffold's empty metric list could
only be filled after a pipeline run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from qanat.models import Metric, Project
from qanat.store import Store, phys


class MetricError(Exception):
    """Something the caller can act on: a formula that will not run, or a clash."""


#: The date bound into `:as_of` when a definition is being checked rather than
#: screened. Any date would do -- what is being tested is whether the statement
#: runs and returns the two columns, not what it returns.
PROBE = "2000-01-01"


def tables_read(project: Project, sql: str) -> list[str]:
    """Which of this project's tables a formula reads.

    A metric names a table by its physical name -- `normalized__facts`, not
    `normalized.facts` -- because the formula is SQL and that is what the
    database calls it.
    """
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql))
    return [ref for ref in project.tables() if phys(ref) in words]


def _unwritten(project: Project, store: Store, sql: str) -> list[str]:
    """Tables the formula reads that this project declares and has not written."""
    return [ref for ref in tables_read(project, sql) if not store.exists(ref)]


def validate_sql(store: Store, project: Project, metric: Metric) -> list[str]:
    """Run the formula. Returns warnings; raises `MetricError` if it cannot run.

    The execution goes through the same function a screen calls, which is the
    whole value of checking here: a definition that passes this has been run by
    the code that will run it again.
    """
    from qanat.screen import ScreenError, metric_values

    try:
        metric_values(store, metric, PROBE)
    except ScreenError as exc:
        waiting = _unwritten(project, store, metric.sql)
        if waiting:
            return [(
                f"not run yet: {', '.join(waiting)} is declared in this project and has "
                f"no rows, so the formula could not be checked against real columns. "
                f"It is stored. Run the pipeline and define it again to have it checked"
            )]
        raise MetricError(str(exc)) from exc
    return []


def define(
    store: Store,
    project: Project,
    root: Path,
    *,
    mid: str,
    sql: str,
    version: int | None = None,
    unit: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Add a definition, or replace one at the same version.

    Omitting `version` on an id that exists is refused rather than guessed at.
    Bumping is a judgement about whether the formula still means what it meant,
    and guessing either way is wrong: silently replacing version N changes what
    a kept answer meant, and silently writing N+1 says the meaning changed when
    it may only have been a typo.
    """
    from qanat.editor import EditorError, save_metric

    if not str(sql or "").strip():
        raise MetricError("a metric is a formula. Pass `sql` selecting subject and value")

    existing = project.metric_versions(mid)
    if version is None:
        if existing:
            raise MetricError(
                f"'{mid}' already has version {max(existing)}. Say which version this is: "
                f"{max(existing)} to replace that formula, or {max(existing) + 1} to write "
                f"a new one beside it. Bumping is a judgement about whether the formula "
                f"still means the same thing, and an answer somebody kept points at a "
                f"version -- so it is not something to guess at"
            )
        version = 1

    prior = project.metric(mid, version)
    try:
        metric = Metric(id=mid, sql=sql, version=int(version), unit=unit or "",
                        description=description or "",
                        origin=prior.origin if prior is not None else "defined")
    except ValueError as exc:
        raise MetricError(str(exc)) from exc

    if prior is not None and prior.origin == "shipped" and prior.sql.strip() != sql.strip():
        raise MetricError(
            f"'{mid}' version {version} is part of the set shipped with this product. "
            f"Write version {max(existing) + 1} instead, which is stored beside it and "
            f"labelled as yours -- a caller's own definition does not join the shipped "
            f"set by being stored next to it"
        )

    warnings = validate_sql(store, project, metric)
    try:
        warnings += save_metric(project, root, metric)
    except EditorError as exc:
        raise MetricError(str(exc)) from exc

    return {
        "defined": mid,
        "version": metric.version,
        "replaced": prior is not None,
        "origin": metric.origin,
        "unit": metric.unit,
        "versions": project.metric_versions(mid),
        "reads": tables_read(project, sql),
        "warnings": warnings,
        "note": (
            "the formula is shared and the threshold you compare it against is yours -- "
            "pass that with the screen call, where it stays"
        ),
    }


def remove(
    project: Project,
    root: Path,
    *,
    mid: str,
    version: int | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Drop a definition, or one version of it.

    **What this cannot check is who still names it, and saying so is the point.**
    A filter is one caller's research criteria and lives on the caller's side of
    the line, so nothing here can see whether a filter still points at this
    metric. The caller can. So dropping the last version of an id is refused
    unless it is asked for in as many words.

    Dropping an older version while a newer one exists is allowed, and warned
    about: an answer pinned to that version stops resolving.
    """
    from qanat.editor import EditorError, delete_metric

    versions = project.metric_versions(mid)
    if not versions:
        known = ", ".join(project.metric_ids()) or "(none defined)"
        raise MetricError(f"no metric called '{mid}'. This project has: {known}")
    if version is not None and int(version) not in versions:
        raise MetricError(
            f"'{mid}' has no version {version}. It has: {', '.join(map(str, versions))}"
        )

    going = versions if version is None else [int(version)]
    staying = [v for v in versions if v not in going]

    if not staying and not force:
        raise MetricError(
            f"removing {'every version of ' if version is None else ''}'{mid}' leaves "
            f"nothing for a filter naming it to resolve, and this server cannot check "
            f"whether one does: a filter is one caller's criteria and is held by the "
            f"caller, not here. Whoever holds it is the only one who knows. Pass "
            f"force: true to remove it anyway"
        )

    try:
        warnings = delete_metric(project, root, mid, version)
    except EditorError as exc:
        raise MetricError(str(exc)) from exc

    if staying:
        warnings = [
            f"version {v} of '{mid}' is gone. An answer pinned to it no longer resolves; "
            f"{', '.join(map(str, staying))} still do"
            for v in going
        ] + warnings
    return {"removed": mid, "versions_removed": going, "versions_left": staying,
            "warnings": warnings}


def listing(project: Project) -> list[dict[str, Any]]:
    """Every definition, newest version of each first, with where it came from."""
    out = []
    for mid in project.metric_ids():
        for v in sorted(project.metric_versions(mid), reverse=True):
            m = project.metric(mid, v)
            out.append({
                "id": m.id, "version": v, "unit": m.unit,
                "description": m.description, "sql": m.sql,
                "origin": m.origin,
                "current": v == max(project.metric_versions(mid)),
            })
    return out


__all__ = ["PROBE", "MetricError", "define", "listing", "remove", "tables_read",
           "validate_sql"]
