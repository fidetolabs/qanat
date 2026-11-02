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

## A metric is a transform definition writing its own table

`the-metric-set-belongs-to-the-qanat-and-the-threshold-to-the-person` decided
that metric values are materialised, keyed by definition version and date. It
named no mechanism, and the mechanism is a `transform_job_definition`:

    normalized.facts --> eps_annual --> metrics.eps_annual
                                              |
    raw.prices -------------------------------+--> pe --> metrics.pe --> pe_rank

**One table per metric, because metrics derive from each other.** A rank reads
the metric it ranks. With one shared `metrics` table that is a transform reading
the table it writes, which `check` refuses -- and which the run order could not
sequence anyway, because it resolves a dependency per *table*, so a shared table
counts as produced the moment any metric writes it. Declared deepest-first, a
three-deep chain scheduled the dependent before its input.

Size does not decide it: at 25M values one long table and twenty separate ones
are both about 190 MB, and a five-metric screen is 5.5 ms against 7.1 ms. What
decides it is that the `from:`/`to:` edge is the **only** record of what a value
was computed from -- there is no row-level input lineage -- so a shared table
makes every derived metric report "I read all metrics".

**And the stage is the registry.** "What metrics exist" is "every table in the
metrics stage", so a metric cannot be declared in one place and missing from
another. There is no list of metric objects to keep aligned.

## A metric carries no version column, and the version is still kept

A literal in generated SQL cannot be trusted to move. Measured: halving a
formula and re-running appended 1,553 new values **all still under
`version: 1`**, leaving 1,553 keys holding two different numbers under one
version. The digest moved correctly; the literal did not, because nothing
connected them.

So the version is the definition's digest, recorded on the execution that ran
(see `Store.start_execution`), and reached from a row through
`_qanat_execution`. That digest is the one that actually produced the row rather
than the one applied today.

## A formula carries its own date. There is no other kind

**A metric has a date of its own.** `PER on 2024-05-03` is
`price(2024-05-03) / EPS(latest filing as of 2024-05-03)` -- one row per subject
per date, which is a series `time_columns` filters exactly as it already filters
facts and spans.

**A formula taking the date as a parameter is refused.** `:as_of` used to mean
"computed when a screen asks, nothing stored", and that kind had to go because
it could not be reached safely: `metric_values` read the table first and fell
back to running the formula when the table returned nothing, and for a dated
series there is no `:as_of` to bind -- so the fallback ran the formula over
*every* row and the screen took whichever came first. Measured on the `check`
project: a screen at 2024-06-15 returned a figure that was not public for five
more months. **The honest answer is that the subject cannot be evaluated**, and
that is what no fallback gives.

The deeper reason is provenance. A value computed on the call leaves no row, so
there is nothing to stamp with the execution that made it -- and a metric that
leaves no row has no lineage at all. The look-ahead was the symptom.

## A formula that cannot run is not stored

Validated by executing it, against this project -- so a definition that
validates is a definition that runs. The database's own message comes back
rather than a definition that fails later, on somebody else's call.

The one thing that is not a refusal is a table the project declares and has not
written yet. A metric defined before the first run is the normal order on a
fresh project, and refusing it would mean a scaffold could only gain metrics
after a pipeline run.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from qanat.models import Project
from qanat.store import Store, phys


class MetricError(Exception):
    """Something the caller can act on: a formula that will not run, or a clash."""


#: What identifies one row of a metric's table. Two columns, because the table
#: is one metric -- its name is the table's name and its version is the digest
#: the execution recorded, so neither is a column that could disagree.
METRIC_KEY = ("subject", "date")

#: Written by `define_metric`, under the project's transforms directory.
SCRIPT_DIR = "transforms/metrics"

#: `:as_of` in a formula, which used to be the caller's date bound in by a
#: screen. Refused now; see the module docstring.
_AS_OF_IN = re.compile(r":as_of\b")

#: What a metric's formula has to select.
REQUIRED = ("subject", "date", "value")


def script_path(mid: str) -> str:
    return f"{SCRIPT_DIR}/{mid}.sql"


def transform_id(mid: str) -> str:
    """The definition that writes one metric.

    **The metric's own name.** It used to be `metric_<id>_v<n>`, which existed to
    keep two versions of one formula apart in a shared table; a metric is its
    own table now and a version is a digest, so there is nothing to disambiguate
    and a second name for the same thing is one more thing to keep aligned.
    """
    return mid


def metrics_stage(project: Project) -> str:
    """The stage metric tables live in, or "" if this project declares none."""
    return project.metrics_stage()


def metric_ref(project: Project, mid: str) -> str:
    """Where one metric's values live."""
    return project.metric_ref(mid)


def takes_a_parameter(sql: str) -> bool:
    """Does this formula want the date handed to it rather than carrying one?"""
    return bool(_AS_OF_IN.search(sql))


def is_series(sql: str, columns: list[str]) -> bool:
    """Does this formula carry a date of its own?

    Read off what it actually returned rather than off the text, because a
    formula can produce `date` from an expression under any spelling of the
    alias.
    """
    if takes_a_parameter(sql):
        return False
    return "date" in {c.lower() for c in columns}


def transform_sql(mid: str, sql: str) -> str:
    """The formula, wrapped into a metric table's shape.

    Generated rather than hand-written, and the file says so: the definition is
    the thing to edit, and a script somebody edited under the generator would be
    overwritten by the next `define_metric` on the same id.

    Three columns out. **No `metric` column** -- the table's name is the metric's
    name. **No `version` column** -- a literal here could not move when the
    formula did, and the version is the digest the execution recorded.
    """
    return (
        f"-- {mid}, generated by define_metric. **Edit the definition, not this\n"
        f"-- file**: the next define_metric on this id overwrites it.\n"
        f"--\n"
        f"-- One row per subject per date. `date` is the metric's own, which is what\n"
        f"-- makes this a series rather than an answer to one call.\n"
        f"SELECT\n"
        f"  _m.subject                  AS subject,\n"
        f"  CAST(_m.date AS DATE)       AS date,\n"
        f"  CAST(_m.value AS DOUBLE)    AS value\n"
        f"FROM (\n{sql.strip().rstrip(';')}\n) AS _m\n"
    )


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


def probe(store: Store, sql: str) -> list[str]:
    """Run the formula once and report the columns it selects.

    Raises `MetricError` with the database's own message if it will not run. The
    statement is executed, not parsed: a formula that passes this has been run
    by the engine that will run it again.
    """
    body = sql.strip().rstrip(";")
    try:
        return list(store.rcon.sql(f"SELECT * FROM ({body}) AS _m LIMIT 0").columns)
    except Exception as exc:
        raise MetricError(f"{type(exc).__name__}: {exc}") from exc


def _materialise(project: Project, root: Path, mid: str, sql: str,
                 unit: str, description: str) -> tuple[str, list[str]]:
    """Write the transform definition and script for one metric. Returns its id."""
    from qanat.editor import EditorError, save_transform

    ref = metric_ref(project, mid)
    if not ref:
        raise MetricError(
            "this project declares no metrics stage, so there is nowhere for a metric "
            "to land. Add a stage with `kind: metrics` to qanat.yaml -- the stage is "
            "the registry, so every table in it is a metric and no list is kept"
        )
    reads = tables_read(project, sql)
    if not reads:
        raise MetricError(
            "the formula reads no table this project declares, so there is nothing for "
            "a transform to depend on and nothing to recompute it from. Name a table "
            "by its physical name, as SQL does -- `normalized__facts`, not "
            "`normalized.facts`"
        )

    path = script_path(mid)
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_text(transform_sql(mid, sql))
    options: dict[str, Any] = {}
    if unit:
        options["unit"] = unit
    if description:
        options["description"] = description
    entry: dict[str, Any] = {
        "id": transform_id(mid),
        "from": reads,
        "to": [ref],
        "script": path,
        # Keyed, so a corrected input writes a new value beside the one somebody
        # was already shown rather than over it.
        "key": list(METRIC_KEY),
    }
    if options:
        entry["options"] = options
    try:
        warnings = save_transform(project, root, entry, create_script=False)
    except EditorError as exc:
        (root / path).unlink(missing_ok=True)
        raise MetricError(
            f"the formula is a series but its transform does not hold against the "
            f"contract: {exc}"
        ) from exc
    return transform_id(mid), warnings


def define(
    store: Store,
    project: Project,
    root: Path,
    *,
    mid: str,
    sql: str,
    unit: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Add a metric, or replace the formula of one that exists.

    **Replacing is not destructive, which is why it needs no version.** The table
    is keyed and never rewritten, so a changed formula appends its values beside
    the old ones; each row names the execution that wrote it, and the execution
    recorded the digest. So the old answer stays readable and stays
    attributable, which is what the version field used to be for -- and unlike
    that field, the digest cannot be left behind when the formula moves.

    It runs straight away. A metric that exists only as a definition answers
    nothing, and asking for it is the next thing any caller does.
    """
    if not str(sql or "").strip():
        raise MetricError(
            "a metric is a formula. Pass `sql` selecting subject, date and value")

    if takes_a_parameter(sql):
        raise MetricError(
            "this formula takes the date as a parameter (`:as_of`), and that kind of "
            "metric is gone. It could not be read safely: with nothing stored a screen "
            "had to run the formula, and a formula with no date to bind ran over every "
            "row -- so a screen at 2024-06-15 could return a figure that was not public "
            "for five more months. Select a `date` column instead, carrying the day each "
            "value became true, and filter the formula's own inputs by it"
        )

    waiting = _unwritten(project, store, sql)
    warnings: list[str] = []
    columns: list[str] = []
    if waiting:
        warnings.append(
            f"not run yet: {', '.join(waiting)} is declared in this project and has no "
            f"rows, so the formula could not be checked against real columns. It is "
            f"stored. Run the pipeline to have it checked and filled"
        )
    else:
        columns = probe(store, sql)
        missing = [c for c in REQUIRED if c not in {x.lower() for x in columns}]
        if missing:
            raise MetricError(
                f"a metric selects subject, date and value; this formula selects "
                f"{', '.join(columns)} and is missing {', '.join(missing)}. "
                f"`date` is the day the value became true -- without it there is no "
                f"series, only an answer to one call"
            )

    existed = project.definition(transform_id(mid)) is not None
    transform, more = _materialise(project, root, mid, sql, unit, description)
    warnings += more

    # Run it, so the metric answers. Reloaded first, because `save_transform`
    # wrote the file this project was read from.
    filled: dict[str, Any] = {"status": "not run", "rows": 0}
    if columns:
        from qanat.project import load
        from qanat.runner import run_definition

        fresh, froot = load(str(root))
        result = run_definition(store, fresh, froot, transform)
        filled = {"status": result.status, "rows": result.rows,
                  "execution": result.execution_id, "error": result.error}
        if not result.ok:
            warnings.append(
                f"the definition is stored but its first run {result.status}: "
                f"{result.error}"
            )

    return {
        "defined": mid,
        "replaced": existed,
        "unit": unit,
        "table": metric_ref(project, mid),
        "reads": tables_read(project, sql),
        "transform": transform,
        "filled": filled,
        # The digest that ran, which is this metric's version. Not a number the
        # caller maintains: it moves when the formula moves, and it is what a
        # row's lineage resolves to.
        "version": (store.definition_version(transform) or {}).get("version"),
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
    force: bool = False,
) -> dict[str, Any]:
    """Drop a metric: its definition, its script, and any cadence over it.

    **What this cannot check is who still names it, and saying so is the point.**
    A filter is one caller's research criteria and lives on the caller's side of
    the line, so nothing here can see whether a filter still points at this
    metric. The caller can. So removing one is refused unless it is asked for in
    as many words.

    The values already written are left where they are. They name the executions
    that wrote them and stay readable, which is the rule a corrected input
    follows too -- removing a definition is not a reason to unsay what somebody
    was already shown.
    """
    from qanat.editor import EditorError, delete_transform

    did = transform_id(mid)
    if mid not in project.metric_ids() and project.definition(did) is None:
        known = ", ".join(project.metric_ids()) or "(none defined)"
        raise MetricError(f"no metric called '{mid}'. This project has: {known}")

    if not force:
        raise MetricError(
            f"removing '{mid}' leaves nothing for a filter naming it to resolve, and "
            f"this server cannot check whether one does: a filter is one caller's "
            f"criteria and is held by the caller, not here. Whoever holds it is the "
            f"only one who knows. Pass force: true to remove it anyway"
        )

    ref = metric_ref(project, mid)
    warnings: list[str] = []
    if project.definition(did) is not None:
        try:
            warnings += delete_transform(project, root, did)
        except EditorError as exc:
            raise MetricError(f"could not remove the transform '{did}': {exc}") from exc
    (root / script_path(mid)).unlink(missing_ok=True)
    warnings.append(
        f"the values already in {ref} are left where they are: they name the executions "
        f"that wrote them, and removing a definition is not a reason to unsay what "
        f"somebody was shown"
    )
    return {"removed": mid, "table": ref, "warnings": warnings}


def listing(project: Project, store: Store | None = None) -> list[dict[str, Any]]:
    """Every metric, read off the stage rather than off a registry.

    **No list is consulted, because there is none.** A metric is a table in the
    metrics stage, so this is what the definitions write -- which means a metric
    cannot be listed here and missing from the pipeline, or the other way round.
    """
    out = []
    for mid in project.metric_ids():
        did = transform_id(mid)
        d = project.definition(did)
        options = dict(getattr(d, "options", {}) or {})
        row: dict[str, Any] = {
            "id": mid,
            "table": project.metric_ref(mid),
            # None when a table sits in the stage with nothing writing it, which
            # is a real state and worth seeing rather than hiding.
            "definition": did if d is not None else None,
            "reads": list(getattr(d, "reads", []) or []),
            "unit": options.get("unit", ""),
            "description": options.get("description", ""),
            "script": getattr(d, "script", None),
            # Several cadences over one definition is ordinary; none means it
            # runs when something asks.
            "schedules": [s.id for s in project.schedules_for(did)],
        }
        if store is not None:
            row["version"] = (store.definition_version(did) or {}).get("version")
        out.append(row)
    return out


__all__ = ["METRIC_KEY", "REQUIRED", "SCRIPT_DIR", "MetricError", "define",
           "is_series", "listing", "metric_ref", "metrics_stage", "probe",
           "remove", "script_path", "tables_read", "takes_a_parameter",
           "transform_id", "transform_sql"]
