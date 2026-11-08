"""What a project holds: its tables and the definitions that fill them.

**Moved out of `mcp.py`** so a second door reads the same answers. The tools
behind `list_tables`, `describe_table`, `sample_table`, `trace_table` and
`list_definitions` worked these out inside the handler -- the freshness check in
particular, which is the one thing that says a metric is a week behind -- and a
web page needing the same answer would have had to copy it. Two copies drift, so
the logic lives here and both doors call it.
"""

from __future__ import annotations

import json
from typing import Any

from qanat.models import Project
from qanat.store import Store


class CatalogError(Exception):
    pass


def known(project: Project, ref: str) -> str:
    if ref in project.tables():
        return ref
    raise CatalogError(
        f"no table '{ref}' in this project. Known tables: "
        f"{', '.join(project.tables()) or '(none)'}"
    )


def tables(store: Store, project: Project) -> dict[str, Any]:
    out = []
    writers = project.writers()
    for ref in project.tables():
        info = store.table_info(ref)
        stage = project.stage(ref.split(".")[0])
        out.append({
            "ref": ref,
            "stage": ref.split(".")[0],
            "stage_kind": stage.kind if stage else None,
            # A list: several definitions may write one table, and a single
            # `producer` string could only name one of them.
            "written_by": writers.get(ref, []),
            "rows": info.rows if info else 0,
            "written": info is not None,
        })
    return {"project": project.name, "tables": out}


def describe(store: Store, project: Project, ref: str) -> dict[str, Any]:
    ref = known(project, ref)
    info = store.table_info(ref)
    if info is None:
        raise CatalogError(
            f"'{ref}' is declared but has not been written yet. Run `run` first.")
    return {
        "ref": ref,
        "rows": info.rows,
        "updated_at": info.updated_at,
        "time_column": store.time_column(ref, project.time_columns.get(ref)),
        "newest": store.max_time(ref, project.time_columns.get(ref)),
        "columns": [{"name": c, "type": t} for c, t in info.columns],
    }


def sample(store: Store, project: Project, ref: str, limit: int = 20,
           as_of: str | None = None) -> dict[str, Any]:
    ref = known(project, ref)
    df = store.read(ref, limit=limit, as_of=as_of or None)
    return {"ref": ref, "as_of": as_of, "rows": len(df),
            "sample": json.loads(df.to_json(orient="records", date_format="iso"))}


def profile(store: Store, project: Project, ref: str) -> dict[str, Any]:
    ref = known(project, ref)
    prof = store.profile(ref)
    if prof is None:
        raise CatalogError(f"{ref} has not been written yet, so there is nothing to "
                           "profile. Run the job that writes it first.")
    return prof


def trace(project: Project, ref: str) -> dict[str, Any]:
    from qanat.project import edges

    ref = known(project, ref)
    es = edges(project)
    upstream = [{"from": a, "definition": d} for a, b, d in es if b == ref]
    downstream = [{"to": b, "definition": d} for a, b, d in es if a == ref]

    reach, seen = [], {ref}
    frontier = [ref]
    while frontier:
        cur = frontier.pop()
        for a, b, _st in es:
            if a == cur and b not in seen:
                seen.add(b)
                reach.append(b)
                frontier.append(b)
    return {"ref": ref, "upstream": upstream, "downstream": downstream,
            "reaches": reach}


def last_date(store: Store, project: Project, ref: str) -> str | None:
    """The newest day a table holds, by its clock, or None if it has none."""
    span = store.time_range(ref, project.time_columns.get(ref))
    return None if span is None else span[1][:10]


def freshness(store: Store, project: Project, definition: Any) -> dict[str, Any]:
    """How far this definition's output has been filled, against its inputs.

    **A stored table only knows the days its last run covered**, and a read
    past that quietly returns an older value: the newest row at or before
    the date asked is still the rule, so a metric that is a week behind
    answers last week's number without saying so. That was the one real
    advantage the computed-on-the-call kind had, and this is the answer to
    it -- saying so rather than computing.

    Only tables with a clock can be compared. One without is not behind or
    current; there is nothing to measure.
    """
    mine = [d for d in (last_date(store, project, w) for w in definition.writes) if d]
    theirs = [d for d in (last_date(store, project, r) for r in definition.reads) if d]
    out: dict[str, Any] = {
        "filled_to": max(mine) if mine else None,
        "inputs_to": max(theirs) if theirs else None,
    }
    if out["filled_to"] and out["inputs_to"]:
        out["behind"] = out["filled_to"] < out["inputs_to"]
    return out


def definitions(store: Store, project: Project, stage: str = "") -> dict[str, Any]:
    """Every definition in dependency order, each with its cadences and, for a
    transform, how far its output is filled against its inputs."""
    from qanat.runner import order

    def cadences(did: str) -> list[dict[str, Any]]:
        # A list, because a definition may carry several. The cadence is not a
        # field on the definition any more, so this is a join rather than a read.
        return [{"id": x.id, "cron": x.cron, "enabled": x.enabled}
                for x in project.schedules_for(did)]

    stage = (stage or "").strip()
    chosen = [x for x in order(project)
              if not stage or any(w.startswith(f"{stage}.") for w in x.writes)]
    rows = [{"id": x.id, "from": x.reads, "to": x.writes, "script": x.script,
             "schedules": cadences(x.id), "options": x.options,
             **freshness(store, project, x)}
            for x in chosen]
    return {
        "ingestion": [] if stage else [
            {"id": x.id, "to": x.writes, "connector": x.connector,
             "schedules": cadences(x.id), "options": x.options}
            for x in project.ingestion_job_definitions],
        "transforms": rows,
    }



def progress(store: Store, project: Project, filer: str,
             limit: int = 20) -> dict[str, Any]:
    """One filer's filings, and how far each has got: raw, facts, spans, metrics.

    **A page needs this and no tool gives it**, so it is here and not on the MCP
    surface: an agent asks `read_company` and reads the counts, and a person
    wants the three marks on one row. Built on `read_company` so the two cannot
    disagree about what is held.

    A filing has *reached* a metric when that metric holds a value for the filer
    dated on or after the day it was filed -- the value a read as of that day
    would see. One aggregate per metric table, never a whole table.
    """
    from qanat.reading import read_company

    held = read_company(store, project, filer, limit=limit, concepts=0)
    who = held["filer"]
    ids = [str(x) for x in (who.get("cik"), who.get("ticker")) if x]
    newest: dict[str, str | None] = {}
    for mid in project.metric_ids():
        ref = project.metric_ref(mid)
        if store.table_info(ref) is None or not ids:
            newest[mid] = None
            continue
        marks = ", ".join("?" for _ in ids)
        got = store.query(
            f"SELECT CAST(max(date) AS VARCHAR) AS d FROM {store.readable(ref)} "
            f"WHERE CAST(subject AS VARCHAR) IN ({marks})", ids)
        d = got["d"].iloc[0] if len(got) else None
        newest[mid] = None if d is None or str(d) in ("NaT", "None", "nan") else str(d)[:10]

    rows = []
    for f in held["filings"]:
        facts = int((f.get("facts") or {}).get("total") or 0)
        sections = f.get("sections")
        reached = sorted(m for m, d in newest.items() if d and d >= f["filed"])
        rows.append({
            "document": f["document"], "form": f["form"], "filed": f["filed"],
            "period_end": f["period_end"],
            "raw": True,
            "facts": facts,
            "sections": None if sections is None else len(sections),
            "metrics": reached,
        })
    return {"filer": who, "metrics": sorted(newest), "filings": rows}


def executions(store: Store, limit: int = 30) -> dict[str, Any]:
    """The most recent executions, newest first."""
    return {"executions": store.recent_executions(int(limit))}


def trace_value(store: Store, project: Project, mid: str, filer: str,
                as_of: str) -> dict[str, Any]:
    """One metric, one company, one date: the value, what it was computed from,
    and the tagged facts of the filing it rests on.

    Read top to bottom it is the chain a person checks a number by: the value a
    screen would see, the tables feeding the metric, and the facts of the last
    filing at or before the date. The value is `metric_values`' -- the same read a
    screen makes -- so this cannot show a number a screen would not.
    """
    from qanat.reading import read_company, read_facts, resolve_filer
    from qanat.screen import metric_values

    if mid not in project.metric_ids():
        raise CatalogError(f"no metric called '{mid}'. This project has: "
                           f"{', '.join(project.metric_ids()) or '(none defined)'}")
    who = resolve_filer(store, project, filer)
    ids = {str(x) for x in (who.get("cik"), who.get("ticker")) if x}
    vals = metric_values(store, project, mid, as_of)
    mine = vals[vals["subject"].astype(str).isin(ids)]
    value = None if mine.empty else mine["value"].iloc[0]
    lineage = trace(project, project.metric_ref(mid))

    filing = None
    facts: dict[str, Any] = {"facts": []}
    held = read_company(store, project, filer, to=as_of, limit=1, concepts=0)
    if held["filings"]:
        filing = {k: held["filings"][0][k] for k in ("document", "form", "filed",
                                                     "period_end")}
        facts = read_facts(store, project, filing["document"], limit=50)
    return {"metric": mid, "filer": who, "as_of": as_of,
            "value": None if value is None else float(value),
            "computed_from": lineage["upstream"], "filing": filing,
            "facts": facts}


def script(project: Project, root: Any, did: str) -> dict[str, Any]:
    """The script one transform runs."""
    known = project.transform_job_definitions
    definition = next((x for x in known if x.id == did), None)
    if definition is None:
        raise CatalogError(
            f"no transform '{did}'. Transforms: {', '.join(x.id for x in known)}")
    path = root / definition.script
    if not path.is_file():
        raise CatalogError(
            f"transform '{did}' points at {definition.script}, which is not on disk")
    return {"id": did, "script": definition.script, "language": path.suffix.lstrip("."),
            "source": path.read_text()}


__all__ = [
    "CatalogError",
    "definitions",
    "describe",
    "executions",
    "freshness",
    "known",
    "last_date",
    "profile",
    "progress",
    "sample",
    "script",
    "tables",
    "trace",
    "trace_value",
]
