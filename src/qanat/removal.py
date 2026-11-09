"""Taking a filing back out of the corpus, and saying what goes with it.

**A removal is a job, not a job definition, and that is the whole shape of this
module.** A definition lives in `jobs.yaml`, has a script or a connector,
declares what it reads and writes, and **a schedule can point at it so it runs
again.** A removal has no script, writes nothing, and must never run twice -- a
standing `delete-this-filing` definition that a cron line could fire walks
straight into *the clock deletes nothing*.

So `jobs.yaml` never changes here and no third definition type exists. What
changes is one word in the run log: `_qanat_job.kind`, until now only ever
`ingestion` or `transform`, also takes `removal`. `schedule_id` is null, which is
the ad-hoc path the store already calls first-class.

**`definition_id` carries the accession**, because a removal has no definition and
the accession is the thing the job is about. That stretches the column's name:
`definition_version` does not resolve for a `removal` row, and a join from
`definition_id` back to `jobs.yaml` finds nothing. The alternative was a column on
`_qanat_job` used by one kind of three, which is worse.

**An as-of read changes retroactively.** Qanat answers as of any past date, and a
removal means an answer somebody was already given stops being reproducible. That
is the real cost and `preview` returns it as a sentence so a caller can show it
rather than bury it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from qanat.models import Project
from qanat.store import Store, sql_literal


class RemovalError(ValueError):
    """A removal that cannot be done, said in a sentence a person can act on."""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _corpus(project: Project) -> tuple[str, str, str]:
    """The three tables a filing occupies, from the project's own `corpus:` block.

    Named by the project rather than guessed, which is the same rule the reading
    tools follow -- the platform does not name layers.
    """
    c = project.corpus
    if c is None or not c.documents:
        raise RemovalError(
            "this project declares no `corpus:` block, so nothing here knows which table "
            "holds its filings. Add `corpus: {documents: ..., facts: ..., spans: ...}` to "
            "qanat.yaml.")
    return c.documents, (c.facts or ""), (c.spans or "")


def _the_filing(store: Store, docs: str, accession: str) -> dict[str, Any]:
    """The one document row, or a refusal naming the accession."""
    got = store.rows_where(docs, f"id = {sql_literal(accession)}", limit=2)
    if not len(got):
        raise RemovalError(
            f"this project holds no filing with accession '{accession}'. "
            f"Call find_filings to see what it does hold.")
    return {k: got.iloc[0][k] for k in got.columns}


def _metric_hits(store: Store, project: Project, entity: Any, when: Any) -> list[dict[str, Any]]:
    """Every metric value computed for this subject on this filing's date.

    **The cascade that surprises people.** A metric row is keyed by subject and
    date, not by document, so nothing in it says which filing it came from. The
    join is resolved here rather than left as "and some metrics".
    """
    out: list[dict[str, Any]] = []
    day = str(when)[:10]
    for mid in project.metric_ids():
        ref = project.metric_ref(mid)
        if not ref or not store.exists(ref):
            continue
        where = (f'subject = {sql_literal(str(entity))} '
                 f"AND CAST(date AS DATE) = DATE {sql_literal(day)}")
        cols = {c for c, _ in (store.table_info(ref).columns if store.table_info(ref) else [])}
        if not {"subject", "date"} <= cols:
            # A metrics table without both columns is not keyed the way a metric
            # is, so nothing here can say which of its rows came from this
            # filing. Asked rather than caught: a swallowed exception here would
            # silently under-report what a removal takes.
            continue
        n = store.count_where(ref, where)
        if n:
            out.append({"metric": mid, "table": ref, "rows": int(n), "where": where})
    return out


def preview(store: Store, project: Project, accession: str) -> dict[str, Any]:
    """What removing this filing would take with it. **Removes nothing.**

    The same show-then-act shape `plan`'s `prune` flag uses, where the rows are
    listed by the same call that would drop them.
    """
    docs, facts, spans = _corpus(project)
    row = _the_filing(store, docs, accession)
    entity, when = row.get("entity"), row.get("published_at")
    lit = sql_literal(accession)

    goes: list[dict[str, Any]] = [{"what": "filing", "table": docs, "rows": 1,
                                   "where": f"id = {lit}"}]
    for label, ref in (("facts", facts), ("sections", spans)):
        if ref and store.exists(ref):
            n = store.count_where(ref, f"document = {lit}")
            if n:
                goes.append({"what": label, "table": ref, "rows": int(n),
                             "where": f"document = {lit}"})
    goes.extend({"what": "metric values", **m} for m in _metric_hits(store, project, entity, when))

    return {
        "accession": accession,
        "filing": {"document": accession, "entity": str(entity) if entity else None,
                   "form": row.get("doc_type"), "filed": str(when)[:10] if when else None,
                   "period_end": str(row.get("period_end"))[:10] if row.get("period_end") else None},
        "goes": goes,
        "rows": sum(int(g["rows"]) for g in goes),
        # Not a footnote. A caller showing this has to show this line too.
        "cost": ("Removing this filing changes answers already given. A read as of a past date "
                 "will no longer return what it returned before, and nothing can put it back "
                 "but ingesting the filing again."),
        "removed": False,
    }


def remove(store: Store, project: Project, root: Path, accession: str) -> dict[str, Any]:
    """Remove a filing and everything derived from it, inside one job.

    The rows removed are the rows `preview` listed, read once and acted on once,
    so the number a person was shown is the number that goes.
    """
    plan = preview(store, project, accession)
    body = _body_path(store, project, accession)

    job_id = store.open_job(accession, "removal", _now(), None)
    targets = sorted({g["table"] for g in plan["goes"]})
    execution_id = store.start_execution(job_id, accession, "removal", targets, None)

    went: list[dict[str, Any]] = []
    try:
        # The filing last, so a crash part-way leaves the document row behind
        # and the removal can be asked for again. The other order leaves facts
        # pointing at a document that is gone, which nothing can find to finish.
        for g in sorted(plan["goes"], key=lambda g: g["what"] == "filing"):
            n = store.delete_where(g["table"], g["where"])
            went.append({"what": g["what"], "table": g["table"], "rows": int(n)})
        if body:
            body.unlink(missing_ok=True)
    except Exception as exc:
        store.end_execution(execution_id, "failed", sum(w["rows"] for w in went),
                            f"{type(exc).__name__}: {exc}")
        raise

    total = sum(w["rows"] for w in went)
    store.end_execution(execution_id, "ok", total)
    store.event("ok", accession, f"removed {total:,} rows for filing {accession}")

    return {"accession": accession, "filing": plan["filing"], "went": went, "rows": total,
            "job": job_id, "execution": execution_id, "removed": True,
            "body_deleted": bool(body), "cost": plan["cost"],
            "next": "the filing is ingestable again; find_filings will report it as not held"}


def _body_path(store: Store, project: Project, accession: str) -> Path | None:
    """The cached primary document on disk, if the connector kept one."""
    docs, _, _ = _corpus(project)
    got = store.rows_where(docs, f"id = {sql_literal(accession)}", limit=1)
    if not len(got) or "body_path" not in got.columns:
        return None
    p = got.iloc[0]["body_path"]
    if not p or str(p) == "nan":
        return None
    path = Path(str(p))
    return path if path.is_file() else None
