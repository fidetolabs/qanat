"""The operator's saved filters, kept in `filters.yaml`.

**A filter is saved clauses for the one screen query, and nothing more.** It
runs on no clock and writes no table. A screen compares clauses against metric
tables that are already materialised, so storing its result would buy nothing
and go stale when a filing lands; `screen_companies` runs a saved filter by name
exactly as it runs the same clauses passed inline.

## Who owns one

**Whoever runs this Qanat**, the same way they own the metric set. A scope is a
capability boundary and not an identity, so nothing here can belong to one user,
and nothing does: a filter in this file is read by every caller of this Qanat.
A reader who wants their own keeps it in their own client and sends the clauses.

This reverses `nothing here stores a filter`. That rule was right while nothing
did -- the name came back unread -- and it is gone because now something does:
fdtl-kb `qanat-keeps-the-operators-filters-in-filters-yaml`.

## No version number

A metric's version is its digest, and an integer beside one was measured
disagreeing with it. A filter takes the same line: the screen answer reports the
clauses it applied, and a caller that keeps a result keeps those.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qanat.models import Filter, Project


class FilterError(Exception):
    pass


#: Where the filters live, relative to the project. Named for messages.
FILE = "filters.yaml"


def listing(project: Project) -> list[dict[str, Any]]:
    """Every saved filter, in file order, as it would be written."""
    return [_row(f) for f in project.filters]


def index(project: Project) -> dict[str, Any]:
    """Every saved filter and the file they live in, as both doors answer."""
    return {"filters": listing(project), "file": FILE}


def get(project: Project, fid: str) -> Filter:
    found = project.filter(fid)
    if found is None:
        have = ", ".join(f.id for f in project.filters) or "(none saved)"
        raise FilterError(f"no filter called '{fid}'. This project has: {have}")
    return found


def clauses_of(project: Project, fid: str) -> list[dict[str, Any]]:
    """A saved filter's clauses in the shape `screen` parses."""
    return [c.model_dump(exclude_none=True) for c in get(project, fid).clauses]


def save(project: Project, root: Path, fid: str, clauses: Any,
         description: str = "") -> dict[str, Any]:
    """Write one filter, replacing one of the same id.

    Every clause is held to what a screen would accept before anything is
    written: the comparison word, a number for the value, a digest rather than an
    integer for a pin, **and a metric this project defines.** A filter that
    cannot run should not be saved and found out about later on somebody's screen.
    """
    from pydantic import ValidationError

    from qanat.editor import EditorError, _edit
    from qanat.project_io import upsert_filter
    from qanat.screen import ScreenError, parse_clauses

    if not isinstance(clauses, list) or not clauses:
        raise FilterError("a filter needs at least one clause: [{metric, op, value}]")
    try:
        parsed = parse_clauses(clauses)
    except ScreenError as exc:
        raise FilterError(str(exc)) from exc
    have = set(project.metric_ids())
    gone = sorted({c.metric for c in parsed} - have)
    if gone:
        raise FilterError(
            f"no metric called {', '.join(gone)}. This project has: "
            f"{', '.join(sorted(have)) or '(none defined)'}")
    try:
        f = Filter(
            id=fid, description=description or "",
            clauses=[{"metric": c.metric, "op": c.op, "value": c.value,
                      **({} if c.version is None else {"metric_version": c.version})}
                     for c in parsed],
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first.get("loc", ()))
        raise FilterError(f"{where or 'filter'}: {first.get('msg')}") from exc

    replaced = project.filter(fid) is not None
    try:
        warnings = _edit(project, root, lambda d: upsert_filter(d, f))
    except EditorError as exc:
        raise FilterError(str(exc)) from exc
    return {"filter": _row(f), "replaced": replaced, "file": FILE, "warnings": warnings}


def remove(project: Project, root: Path, fid: str) -> dict[str, Any]:
    from qanat.editor import EditorError, _edit
    from qanat.project_io import remove_filter

    get(project, fid)
    try:
        warnings = _edit(project, root, lambda d: remove_filter(d, fid))
    except EditorError as exc:
        raise FilterError(str(exc)) from exc
    return {"removed": fid, "file": FILE, "warnings": warnings}


def _row(f: Filter) -> dict[str, Any]:
    return {
        "id": f.id,
        "description": f.description,
        "clauses": [c.model_dump(exclude_none=True) for c in f.clauses],
    }


__all__ = ["FILE", "FilterError", "clauses_of", "get", "index", "listing", "remove", "save"]
