"""Screen: which companies passed, on a date or across a window.

A screen is a query. It reads the facts, computes the metrics a filter names,
applies the thresholds, and returns the rows that passed. **It writes no
table.** Run it again for another date and it computes again.

There is no results layer for the same reason there is no metrics layer: a
result belongs to whoever set the threshold, and a store that cannot tell two
callers apart has nowhere to put one correctly.

**One date or a window, one verb.** A window of one is a single date, and the
rows come back the same shape either way, so a caller never has to branch on
which form it asked for.

    screen(store, project, clauses, as_of="2025-12-31")
    screen(store, project, clauses, frm="2021-01-01", to="2025-12-31")

**The two halves of a clause.** A metric is a formula and is shared, because
`price / diluted EPS` is arithmetic with no judgement in it. A threshold is an
opinion and belongs to whoever set it. A clause pairs them, and only the metric
half comes out of the project.

## A filter is a named object, and the name is the caller's

A filter is several clauses under one name -- the thing somebody thinks of as
*my rule*. **It is passed in, never stored here.** A filter is one caller's
research criteria rather than a financial dataset, and a server that cannot tell
two callers apart has nowhere to keep one correctly: stored here, one person's
preference would be everybody's. Holding them would also mean taking on the
research workspace of every application that ever connects -- portfolio
screening, factor research, thesis tracking -- and that coupling is worth
avoiding.

So the division is: **this platform owns the data, the definitions and the
running of them; the caller owns the filter, the history and the results.**

**Which puts an obligation on this end, and it is the whole of what makes the
split workable.** A caller that keeps its own history can only keep what it was
given. So a screen returns the companies, **the metric values behind the
verdict**, the definition versions it actually resolved, and an id for the
inputs -- enough to store a record it can read back later without asking
again. Returning a bare pass/fail would make the caller re-query for the numbers
it had just paid to compute.

**What a screen takes is clauses, not a filter.** A filter is the caller's named
rule and nothing here stores one, so there is no name to send:

    clauses=[{"metric": "pe", "metric_version": 3, "op": "lt", "value": 15},
             {"metric": "net_margin", "op": "gte", "value": 0.2}]

It used to accept `[{filter, version, clauses}]` as well, and **that wrapper did
nothing at all**: every clause was flattened into one verdict before anything
was judged, so two filters and one concatenated list gave identical answers, and
the name and version were echoed into `applied` and read nowhere. A meaningless
`version` therefore rode through and was recorded as if it meant something.

`applied` still carries what the caller could *not* have known -- the definition
version each clause actually read. Not what they sent.

**A clause may pin the metric version it meant.** Without one it reads the
current definition, which is right for a fresh screen and wrong for a rule
written against version 3 when version 4 now exists. With one, bumping a metric
cannot silently change what a saved filter computes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from qanat.models import Project
from qanat.store import EXEC_COL, SEQ_COL, Store


#: Stands in for a table that is not there, so the column check reads the same
#: way whether the table is missing a column or missing entirely.
class _NoColumns:
    columns: tuple[str, ...] = ()


_NO_COLUMNS = _NoColumns()


class ScreenError(Exception):
    pass


#: The comparisons a clause may make. Spelled as words as well as symbols,
#: because an agent writing JSON reaches for one and a person reaches for the
#: other.
OPERATORS: dict[str, Any] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


@dataclass(frozen=True)
class Clause:
    """One metric against one threshold.

    The metric half is shared and named here. The threshold half is the
    caller's and arrives with the call, never out of the project file.

    `version` pins which definition of the metric this clause meant. Left out,
    the clause reads the current one -- right for a screen somebody is running
    now, wrong for a rule that was written against version 3 when version 4
    exists. **A filter that pins its versions cannot be changed by somebody
    bumping a formula.**
    """

    metric: str
    op: str
    value: float
    #: The definition digest this clause insists on, or None for whatever is
    #: current. **A digest, not a number somebody maintained.** A metric has no
    #: version column: the version is the digest its execution recorded, and
    #: that is what `metric_version_used` hands back for keeping.
    version: str | None = None

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise ScreenError(
                f"no comparison called '{self.op}'. The ones there are: "
                + ", ".join(sorted(OPERATORS))
            )

    @property
    def key(self) -> tuple[str, str | None]:
        """What identifies the column this clause reads: the metric at a version."""
        return (self.metric, self.version)

    def as_applied(self, resolved: dict[str, str | None] | None = None) -> dict[str, Any]:
        """What a caller records about this clause, as it ran.

        **`metric_version_used` is the point of it**: the definition digest the
        clause actually read. `metric_version` is what the caller pinned and may
        be absent; this one ran and never is. A caller keeping its own history
        can only keep what it was handed, and that is the half it could not have
        known -- unlike a filter name, which it sent.

        Hand `metric_version_used` back as `metric_version` to read the same
        answer again. That is what replaced bumping an integer: the digest moves
        when the formula moves, so it cannot be left behind.
        """
        resolved = resolved or {}
        return {
            "metric": self.metric, "op": self.op, "value": self.value,
            **({} if self.version is None else {"metric_version": self.version}),
            **({} if self.column not in resolved
               else {"metric_version_used": resolved[self.column]}),
        }

    @property
    def column(self) -> str:
        """The name this clause's values go under while the screen runs."""
        return self.metric if self.version is None else f"{self.metric}@{self.version}"

    def test(self, series: pd.Series) -> pd.Series:
        return OPERATORS[self.op](series, self.value)

    def __str__(self) -> str:
        at = "" if self.version is None else f"@{self.version}"
        return f"{self.metric}{at} {self.op} {self.value:g}"


def parse_clauses(raw: Any) -> list[Clause]:
    """One flat list of conditions, which is all a screen can act on.

    It used to also accept `[{filter, version, clauses}]` -- a named filter
    wrapping its clauses. **The wrapper did nothing.** Every clause from every
    filter was flattened into one verdict before anything was judged, so two
    filters and one concatenated list gave identical answers; and the name and
    version were echoed into `applied` and read nowhere, so a meaningless
    `version` rode through and was recorded as if it meant something.

    The nested shape is still refused rather than flattened. **A saved filter is
    named at the top of the call, not inside the clause list** -- `run` takes
    `filter` or `clauses` -- so a wrapper in here is somebody reaching for the
    old shape, and the refusal says where the name goes now.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ScreenError("clauses is a list of {metric, op, value}")
    out = []
    for c in raw:
        if not isinstance(c, dict):
            raise ScreenError(f"a clause must be an object, got {type(c).__name__}")
        if "clauses" in c or "filter" in c:
            raise ScreenError(
                "a clause is {metric, op, value}, not a named filter wrapping "
                "clauses. Pass the clauses themselves, or name a saved filter "
                "with `filter` instead of `clauses` -- the grouping was never "
                "honoured, because every clause has to pass"
            )
        out.append(_clause(c))
    return out


def _clause(c: dict) -> Clause:
    missing = [k for k in ("metric", "op", "value") if k not in c]
    if missing:
        raise ScreenError(f"a clause needs {', '.join(missing)}: {c}")
    # `metric_version` is the spelling that comes back in `applied`, and
    # `version` is the one a caller reaches for. Both are read; a filter's own
    # version is a different field on a different object.
    pinned = c.get("metric_version", c.get("version"))
    try:
        value = float(c["value"])
    except (TypeError, ValueError) as exc:
        raise ScreenError(f"clause value must be a number: {c}") from exc
    if isinstance(pinned, int) and not isinstance(pinned, bool):
        raise ScreenError(
            f"metric_version is a definition digest, not a number: got {pinned}. A "
            f"metric has no version column any more -- the version is the digest the "
            f"execution recorded. Take it from `metric_version_used` in a previous "
            f"answer's `applied`, or leave it out for whatever is current"
        )
    return Clause(metric=str(c["metric"]), op=str(c["op"]), value=value,
                  version=None if pinned is None else str(pinned))


# ------------------------------------------------------------------ the metrics
def metric_values(store: Store, project, mid: str, as_of: str,
                  version: str | None = None) -> pd.DataFrame:
    """One metric for every subject, as it stood on one date.

    **Read from the table, and only from the table.** A metric is a transform's
    output, so the value a screen reports is the value that was written -- which
    means it names the execution that wrote it and can be traced. A number
    computed on the call leaves no row, so there would be nothing to trace and
    nothing to compare a later answer against.

    **The newest date at or before `as_of`, per subject.** A metric is a series,
    so "the value on that date" means the most recent one the series had reached
    by then -- not an exact match, which would answer nothing on a day the
    subject had no new filing. A trading-day series answers the day itself; a
    filing-dated one answers the last filing. One rule, both calendars, and a
    Saturday correctly falls back to Friday.

    **A subject with no row at or before the date is simply absent**, and the
    screen reports it as not evaluated. There used to be a fallback that ran the
    formula when the table returned nothing, and it was unsafe: a dated series
    has no `:as_of` to bind, so the fallback ran over every row and the screen
    took whichever came first. Measured on the `check` project, a screen at
    2024-06-15 returned a figure that was not public for five more months.

    `version` pins the digest that must have produced the row -- the one
    `lineage` reports -- so a kept answer can be read back as it stood. Rows
    from an execution that recorded no digest are excluded when pinning, because
    including them would be a guess.

    Which *row* for a date is the store's answer, not this one's: two rows for
    one key are a correction, and the `now` view returns the newest write.
    """
    ref = project.metric_ref(mid) if project is not None else ""
    if not ref:
        raise ScreenError(
            f"this project declares no metrics stage, so '{mid}' has nowhere to live. "
            f"Add a stage with `kind: metrics` to qanat.yaml"
        )
    if not store.exists(ref):
        raise ScreenError(
            f"metric '{mid}' has no values yet: {ref} has not been written. Run its "
            f"definition -- a metric answers from its table, and there is no longer a "
            f"path that computes one on the call"
        )

    # `columns` is (name, type) pairs.
    held = {str(c[0]).lower() for c in (store.table_info(ref) or _NO_COLUMNS).columns}
    missing = [c for c in ("subject", "date", "value") if c not in held]
    if missing:
        raise ScreenError(
            f"{ref} must hold subject, date and value; it holds "
            f"{', '.join(sorted(held)) or '(nothing)'} and is missing "
            f"{', '.join(missing)}"
        )

    where, params = ["CAST(date AS DATE) <= CAST(? AS DATE)"], [as_of]
    if version:
        # **Read the physical table, not the `now` view.** Pinning asks for the
        # rows one execution wrote, and some of those are rows the view treats
        # as superseded -- which is the whole point of being able to pin.
        reading = store.raw_ref(ref)
        order = f'date DESC, "{SEQ_COL}" DESC'
        # Cast, because a table whose every execution is null -- one written
        # entirely outside a job -- reads back as VARCHAR.
        where.append(
            f'CAST("{EXEC_COL}" AS BIGINT) IN '
            f"(SELECT execution_id FROM {store.executions_ref()} "
            f"WHERE definition_version = ?)")
        params.append(version)
    else:
        reading, order = store.readable(ref), "date DESC"

    try:
        df = store.query(
            f"SELECT subject, value FROM ("
            f"  SELECT subject, value, row_number() OVER ("
            f"    PARTITION BY subject ORDER BY {order}) AS rn"
            f"  FROM {reading}"
            f"  WHERE {' AND '.join(where)}"
            f") WHERE rn = 1", params)
    except Exception as exc:
        raise ScreenError(f"metric '{mid}' could not be read from {ref}: {exc}") from exc

    cols = {c.lower(): c for c in df.columns}
    out = df[[cols["subject"], cols["value"]]].copy()
    out.columns = ["subject", "value"]
    # A pin that matches nothing comes back empty, and an empty frame's columns
    # are typed by guesswork -- which then refuses to merge against the subject
    # list. Pinned to a digest nobody recorded, "no rows" is the right answer,
    # so it has to survive being joined.
    out["subject"] = out["subject"].astype(object)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out


# ---------------------------------------------------------------- the membership
@dataclass
class Membership:
    """Which subjects a screen may return, and whether each name was checked.

    **`companies` is the caller's list and this platform's work to place.** Which
    companies somebody cares about is their business -- it is named on the
    client's side of the line, along with their filters and their history -- so
    this takes the list with the call and keeps none of it. **Placing those
    names against the filers this corpus holds, on the right date, is the part
    that is not their business**, and it is the whole of what this does.

    Two ways in, and the distinction is who owns the list. `companies` is passed
    inline and never stored: that is Margiana's list, or the list an agent built
    against open-source Qanat, and storing it would mean this end holding one
    caller's opinion. `from_universe` names a csv under `./universes/`, which
    `define_universe` writes at `full` scope and an ingestion job already reads
    -- **deployment config, not caller state**, the same standing as a metric
    formula. Naming that one is not a lookup of somebody's private list.

    An earlier version took the name of a *caller's* universe out of the project
    file, which was the same mistake as storing a filter, and it is still
    refused. What changed on 2026-11-02 is only that a list this deployment
    already owns for fetching may also bound a screen.
    """

    members: pd.DataFrame
    unknown: list[str] = field(default_factory=list)
    point_in_time: bool = False
    checked: bool = True

    def on(self, as_of: str) -> list[str]:
        """The subjects in this universe on one date, in order."""
        from qanat.context import members as members_on

        rows = members_on(self.members, as_of if self.point_in_time else None)
        return sorted({str(s) for s in rows["subject"] if s})

    def as_applied(self) -> dict[str, Any]:
        """What this universe was, for a caller to store beside its answer.

        **`matched` is null when nothing was looked up**, rather than the count
        of names that went through. It said `placed: 2` once, for a project with
        no index to place anything against -- which reads as "I found both of
        them" when the truth is "I looked for neither". A number that is only
        meaningful under a condition has to be absent when the condition fails,
        or the condition is the thing nobody reads.
        """
        return {
            "asked": len(self.members) + len(self.unknown),
            # Found in the company index. None when there was no index.
            "matched": len(self.members) if self.checked else None,
            "unknown": self.unknown,
            "point_in_time": self.point_in_time,
            # False when this project holds no company index to check against.
            # A name that was never checked is not a name that passed a check.
            "checked": self.checked,
        }


def symbols_from_universe(named: str, root: Any) -> list[str]:
    """The symbols in a named universe, read the way an ingestion job reads them.

    One resolver for both callers. A screen that found a universe by a different
    rule than the fetch job does would be a second meaning for one name.
    """
    import pandas as _pd

    from qanat.connectors.sec import universe_path

    path = universe_path(str(named), root)
    out = [str(x).strip() for x in _pd.read_csv(path)["symbol"].tolist() if str(x).strip()]
    if not out:
        raise ScreenError(f"universe '{named}' is empty: {path} holds no symbol")
    return out


def read_membership(store: Store, project: Project,
                    companies: Sequence[Any]) -> Membership:
    """Place a list of names against this corpus.

    Accepts plain symbols -- tickers or CIKs -- or rows carrying `from` and `to`
    so membership is point-in-time:

        ["AAPL", "MSFT"]
        [{"symbol": "AAPL", "from": "2021-01-01"}, {"symbol": "KO", "to": "2023-06-01"}]

    **A name this corpus does not hold is reported, never dropped.** A list of
    fifty where ten are not held is a screen that quietly answered for forty,
    which is the same class of lie as a window that came back short.
    """
    from qanat.context import is_point_in_time
    from qanat.reading import company_index

    if isinstance(companies, (str, bytes)):
        raise ScreenError(
            "`companies` is a list of names, not one name. Pass [\"AAPL\"] rather than "
            "\"AAPL\" -- and a list is all it takes, because which companies you care "
            "about is yours to keep, not this project's"
        )
    rows: list[dict[str, Any]] = []
    for entry in companies:
        if isinstance(entry, dict):
            symbol = str(entry.get("symbol") or entry.get("subject") or "").strip()
            if not symbol:
                raise ScreenError(f"a `companies` entry needs a `symbol`: {entry}")
            rows.append({"symbol": symbol, "from": entry.get("from"),
                         "to": entry.get("to")})
        else:
            rows.append({"symbol": str(entry).strip()})
    rows = [r for r in rows if r["symbol"]]
    if not rows:
        raise ScreenError("`companies` needs at least one name")

    raw = pd.DataFrame(rows)
    index = company_index(store, project)

    subjects: list[str | None] = []
    unknown: list[str] = []
    for symbol in raw["symbol"].astype(str):
        placed = index.place(symbol)
        if placed is not None:
            subjects.append(placed)
        elif index.known:
            # There is an index and it does not hold this name. That is a fact
            # about the corpus, and it is reportable.
            subjects.append(None)
            unknown.append(symbol)
        elif symbol.lstrip("0").isdigit():
            subjects.append(f"{int(symbol):010d}")
        else:
            # No index to check against. A subject is not required to be a
            # filer -- the SEC corpus is the first dataset here, not the
            # subject -- so the name is taken as written and `checked` says so.
            subjects.append(symbol)

    placed_rows = raw.assign(subject=subjects)
    return Membership(
        members=placed_rows[placed_rows["subject"].notna()].reset_index(drop=True),
        unknown=unknown,
        point_in_time=is_point_in_time(raw),
        checked=index.known,
    )


# ------------------------------------------------------------------ the coverage
def coverage(store: Store, project: Project,
             metrics: Sequence[str] | None = None) -> dict[str, Any]:
    """The dates a screen can actually be answered for, and what sets the bound.

    **A screen that quietly answered for three of the five years asked for has
    told the caller something false**, which is the same reason `counts` exists.
    So the answerable range is a thing a caller can read before it screens,
    rather than something it discovers from a list that came back short.

    The bound comes from the data the metrics read. A point-in-time read as of
    date D sees every row published on or before D, so:

    * **before the oldest row there was nothing public.** A screen there has no
      corpus to read and returns an empty list that looks like a real answer.
    * after the newest row, every date reads the same rows as the newest one.
      Not false -- just flat, and worth saying rather than refusing.
    """
    from qanat.metrics import transform_id

    which = list(metrics) if metrics else project.metric_ids()
    refs: list[str] = []
    for mid in which:
        # What a metric reads is declared on its definition, not parsed out of a
        # formula this end no longer holds.
        d = project.definition(transform_id(mid))
        if d is not None:
            refs.extend(d.reads)
        ref = project.metric_ref(mid)
        if ref:
            refs.append(ref)
    # The returns table is read too, and a screen reports a forward return off it.
    if project.returns is not None:
        refs.append(project.returns.table)

    tables = []
    for ref in dict.fromkeys(refs):
        span = store.time_range(ref, project.time_columns.get(ref))
        tables.append({
            "ref": ref,
            "from": None if span is None else span[0][:10],
            "to": None if span is None else span[1][:10],
            "written": store.exists(ref),
        })

    dated = [x for x in tables if x["from"]]
    # The widest window every table can answer for is the latest start and the
    # earliest end. A metric reading two tables cannot answer before the second
    # one begins.
    begins = max((x["from"] for x in dated), default=None)
    ends = max((x["to"] for x in dated), default=None)
    return {
        "metrics": which,
        "from": begins,
        "to": ends,
        "tables": tables,
        "why": (
            "a read as of a date sees the rows published on or before it, so before "
            f"{begins} there is nothing to read" if begins else
            "nothing the metrics read has been written yet, so there is no coverage. "
            "Run the pipeline"
        ),
    }


def _check_window(store: Store, project: Project, stops: list[str],
                  wanted: Sequence[str]) -> list[str]:
    """Refuse a window this corpus cannot answer for. Returns warnings.

    A date before the corpus begins is refused rather than answered, because an
    empty list and a list of nobody look the same and mean different things.
    """
    have = coverage(store, project, wanted)
    begins, ends = have["from"], have["to"]
    if begins is None:
        return []

    early = [d for d in stops if str(d)[:10] < begins]
    if early:
        held = ", ".join(
            f"{x['ref']} {x['from']} to {x['to']}" for x in have["tables"] if x["from"])
        raise ScreenError(
            f"this project cannot answer for {', '.join(str(d)[:10] for d in early)}: "
            f"nothing was published before {begins}, so a screen there reads an empty "
            f"corpus and returns a list of nobody -- which is not the same answer as "
            f"nobody passing. It covers {begins} to {ends}. What it holds: {held}. "
            f"Ask for a window inside that, or call `check_coverage` first"
        )

    late = [str(d)[:10] for d in stops if ends and str(d)[:10] > ends]
    if late:
        return [(
            f"{', '.join(late)} is past the newest row ({ends}), so those dates read the "
            f"same corpus as {ends} and repeat its answer. Not wrong -- just not new"
        )]
    return []


# ------------------------------------------------------------------- the return
def forward_returns(store: Store, project: Project) -> pd.DataFrame:
    """What each company did between its own dates.

    One row per (subject, date): the price then, the price at that subject's
    **next** date, and the return between them.

    The dates are each company's own. Costco files in October and Walmart in
    March, so there is no shared calendar to price them on, and inventing one
    would measure twelve months for one name against sixteen for another.

    Nothing here adds returns together. Doing that needs a decision about how
    much of each company to hold, and that decision belongs to whoever is
    holding them.
    """
    r = project.returns
    if r is None:
        return pd.DataFrame(columns=["subject", "as_of", "price", "next_as_of",
                                     "next_price", "forward_return"])
    if not store.exists(r.table):
        raise ScreenError(
            f"returns.table is '{r.table}' and nothing has written it yet. "
            "Run the pipeline first, or point it at a table that exists."
        )
    df = store.read(r.table)
    missing = [c for c in (r.subject_column, r.date_column, r.price_column)
               if c not in df.columns]
    if missing:
        raise ScreenError(
            f"'{r.table}' has no column(s) {', '.join(missing)}. It holds: "
            + ", ".join(df.columns)
        )
    out = df[[r.subject_column, r.date_column, r.price_column]].copy()
    out.columns = ["subject", "as_of", "price"]
    out["as_of"] = pd.to_datetime(out["as_of"])
    out["price"] = pd.to_numeric(out["price"], errors="coerce")
    out = out.dropna(subset=["price"]).sort_values(["subject", "as_of"])
    out["next_as_of"] = out.groupby("subject")["as_of"].shift(-1)
    out["next_price"] = out.groupby("subject")["price"].shift(-1)
    out["forward_return"] = out["next_price"] / out["price"] - 1.0
    return out.reset_index(drop=True)


def _attach_returns(rows: list[dict], fwd: pd.DataFrame) -> None:
    """Give each screened row the return of the company's own next filing.

    A screen stops at a calendar date; a company's return runs from its own
    filing. So the one that counts is the most recent filing at or before the
    date screened.
    """
    if fwd.empty:
        return
    by_subject = {s: g for s, g in fwd.groupby("subject")}
    for row in rows:
        g = by_subject.get(row["subject"])
        if g is None:
            continue
        upto = g[g["as_of"] <= pd.Timestamp(row["as_of"])]
        if upto.empty:
            continue
        hit = upto.iloc[-1]
        row["priced_on"] = str(hit["as_of"].date())
        row["forward_return"] = (
            None if pd.isna(hit["forward_return"]) else float(hit["forward_return"])
        )
        row["next_priced_on"] = (
            None if pd.isna(hit["next_as_of"]) else str(hit["next_as_of"].date())
        )


# ------------------------------------------------------------------- the screen
#: What one screened row looks like, whatever the span. One date is a window of
#: one, so a caller never branches on the shape it got back.
COLUMNS = ["as_of", "subject", "ticker", "name", "passed", "failed_on", "evaluated"]


def screen(
    store: Store,
    project: Project,
    clauses: Sequence[Clause],
    as_of: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    every: str = "1y",
    companies: Sequence[Any] | None = None,
    from_universe: str | None = None,
    root: Any = None,
) -> dict[str, Any]:
    """Who passed, on one date or on every date in a window.

    Returns what a screen may report, and nothing else:

    | `rows`      | one row per subject per date, the verdict, and the values |
    | `counts`    | per date: how many could be evaluated, how many could not |
    | `applied`   | the clauses as they ran, with the version each one read |
    | `inputs`    | the definition versions it read, and the corpus it read them from |

    No average across dates, no comparison against the companies that failed,
    no score. A number that cannot be read off one date's list is not here.

    **`companies` bounds who is considered**, as a list of tickers or CIKs you
    pass with the call. **It widens the answer as well as narrowing it**: a name
    you asked about that has no value for a metric the filter names comes back as
    a row that could not be evaluated, rather than being absent. Without it there
    is no way to know who is missing -- the subjects are whoever the metrics
    returned -- and "nobody else qualified" and "I could not judge nine of them"
    are not the same answer.

    `from_universe` is the other way in: the name of a csv under `./universes/`
    that this deployment already holds, so a list of two hundred is not pasted
    into every call. **One or the other, never both** -- two lists in one call
    has no obvious answer, and guessing at a union would bound a screen by
    something the caller did not write.

    `list_companies` is what a caller builds `companies` from.
    """
    from qanat.dates import WindowError, window

    if as_of and (frm or to):
        raise ScreenError("give either as_of, or from and to. Not both.")
    if not as_of and not (frm and to):
        raise ScreenError(
            "a screen needs a date: either as_of for one, or from and to for a window"
        )
    try:
        stops = [as_of] if as_of else window(frm, to, every)
    except WindowError as exc:
        raise ScreenError(str(exc)) from exc
    from qanat.metrics import transform_id

    if not stops:
        raise ScreenError("that window holds no dates")

    # One entry per metric *at a version*, because two clauses may read two
    # versions of one formula -- which is the whole reason a clause may pin one.
    wanted: dict[tuple[str, str | None], str] = {}
    for c in clauses:
        wanted[c.key] = c.column

    have = set(project.metric_ids())
    missing = sorted({mid for mid, _v in wanted} - have)
    if missing:
        known = ", ".join(sorted(have)) or "(none defined)"
        plural = "s" if len(missing) > 1 else ""
        raise ScreenError(
            f"no metric{plural} called {', '.join(repr(m) for m in missing)}. "
            f"This project has: {known}"
        )

    picked: Membership | None = None
    if companies is not None and from_universe is not None:
        raise ScreenError(
            "pass `companies` or `from_universe`, not both. One is your list and the "
            "other is a list this project holds; a call carrying two has no obvious "
            "answer, and a union of them would bound the screen by something you did "
            "not write"
        )
    named: str | None = None
    if from_universe is not None:
        if root is None:
            raise ScreenError(
                "`from_universe` names a file, so this call needs the project root. "
                "Pass `companies` instead when calling screen() directly"
            )
        named = str(from_universe)
        try:
            companies = symbols_from_universe(named, root)
        except (ValueError, KeyError) as exc:
            raise ScreenError(str(exc)) from None
    if companies is not None:
        picked = read_membership(store, project, companies)
        if not len(picked.members):
            raise ScreenError(
                f"this project holds none of those {len(picked.unknown)} name(s): "
                f"{', '.join(picked.unknown[:10])}. Call list_companies to see which "
                f"companies it does hold, or find_filings and ingest_filings to add these"
            )

    notes = _check_window(store, project, list(stops), [mid for mid, _v in wanted])
    if picked is not None:
        if picked.unknown:
            notes.append(
                f"{len(picked.unknown)} name(s) are not in this project and are not in "
                f"the answer: {', '.join(picked.unknown[:10])}"
                + ("..." if len(picked.unknown) > 10 else "")
                + ". Call list_companies to see what it holds"
            )
        if not picked.checked:
            notes.append(
                "this project declares no documents table, so there was no company "
                "index to check these names against. They were taken as written -- a "
                "name that was never checked is not a name that passed a check"
            )
        if not picked.point_in_time:
            notes.append(
                "this list carries no join and leave dates, so it is today's "
                "membership applied to every date screened. Every number built on it is "
                "flattered by the names that survived"
            )

    # Which definition each clause actually read. An unpinned clause reads the
    # current version, so recording only what the caller pinned would leave a
    # kept answer unable to say what computed it -- and then it is not a record.
    resolved = {
        col: version or (store.definition_version(transform_id(mid)) or {}).get("version")
        for (mid, version), col in wanted.items()
    }

    columns = list(wanted.values())
    from qanat.reading import company_index

    rows: list[dict] = []
    counts: list[dict] = []
    # A screen hands back a filer id, and nobody reads one. The index that
    # places the names a caller passed already knows the reverse, so the ticker
    # and the company name travel with the verdict instead of costing a
    # `list_companies` call per row.
    labels = company_index(store, project)
    for stop in stops:
        values = {
            col: metric_values(store, project, mid, stop, version)
            for (mid, version), col in wanted.items()
        }
        # Without a universe the subjects are whoever the metrics returned.
        # With one they are the membership on this date, so a member the
        # metrics say nothing about is still counted -- as unjudgeable.
        if picked is None:
            subjects = sorted({s for df in values.values() for s in df["subject"]})
        else:
            subjects = picked.on(stop)
        # Typed, because an empty list of subjects -- every metric pinned to a
        # digest nobody recorded -- otherwise comes out float64 and refuses to
        # merge against the string subjects on the other side.
        table = pd.DataFrame({"subject": pd.Series(subjects, dtype=object)})
        for col, df in values.items():
            table = table.merge(df.rename(columns={"value": col}), on="subject", how="left")

        # A subject missing any metric the filter names cannot be judged. It is
        # not a fail. Saying so is the count, and the count is why a list is a
        # claim rather than a rumour.
        evaluable = table[columns].notna().all(axis=1) if columns else pd.Series(
            True, index=table.index)

        passed = pd.Series(True, index=table.index)
        failed_on: list[list[str]] = [[] for _ in range(len(table))]
        # **Every clause has to pass.** That is why there was nothing for the
        # old filter grouping to express: two filters and one concatenated list
        # reach the same verdict, because the AND is over all of them either way.
        for c in clauses:
            ok = c.test(table[c.column])
            for i in table.index[~ok.fillna(False)]:
                failed_on[i].append(str(c))
            passed &= ok.fillna(False)

        for i, subject in enumerate(table["subject"]):
            tick, nm = labels.label(subject)
            rows.append({
                "as_of": stop,
                "subject": subject,
                # None, not "", when the corpus carries no `source_keys` to read
                # a name out of: absent and unknown are different answers.
                "ticker": tick,
                "name": nm,
                "passed": bool(passed.iloc[i]) if bool(evaluable.iloc[i]) else None,
                "failed_on": ", ".join(failed_on[i]),
                "evaluated": bool(evaluable.iloc[i]),
                # The numbers the verdict was reached on. A caller keeping its
                # own history can only keep what it is handed, and a bare
                # pass/fail would make it ask again for the values this call
                # has already computed. Keyed by the clause's column, so two
                # versions of one metric stay apart.
                "values": {
                    col: (None if pd.isna(table[col].iloc[i])
                          else float(table[col].iloc[i]))
                    for col in columns
                },
            })
        counts.append({
            "as_of": stop,
            "subjects": len(table),
            "evaluated": int(evaluable.sum()),
            "dropped": int((~evaluable).sum()),
            "passed": int((passed & evaluable).sum()),
            # Said out loud rather than left to be inferred from three zeros. A
            # date nothing could be judged on is a different answer from a date
            # where nobody passed, and a row of zeros reads as the second one.
            "nothing_evaluable": int(evaluable.sum()) == 0,
        })

    # The second of the three outputs. Absent when the project does not say
    # where a price lives, because a screen is still a screen without one.
    _attach_returns(rows, forward_returns(store, project))

    out: dict[str, Any] = {
        "rows": rows,
        "counts": counts,
        "applied": [c.as_applied(resolved) for c in clauses],
        "inputs": _inputs(store, project, resolved, stops, as_of, frm, to, every,
                          picked),
    }
    if picked is not None:
        # Named `companies`, matching the argument it reports on. `from_universe`
        # says which stored list the names came from, or null when the caller
        # passed them inline -- so an answer records where its bound came from.
        applied = dict(picked.as_applied())
        applied["from_universe"] = named
        out["companies"] = applied
    if notes:
        out["notes"] = notes
    return out


def run(
    store: Store,
    project: Project,
    *,
    filter: str | None = None,
    clauses: Any = None,
    **kw: Any,
) -> dict[str, Any]:
    """A screen by saved filter or by clauses. **One or the other, never both.**

    Both doors call this, so the rule is held once. Two at once is refused rather
    than merged, so an answer never has to say which threshold won -- somebody
    tweaking a saved filter loads its clauses, changes them, and sends clauses.

    The answer names what it ran: `filter` is the saved id, or None for clauses
    passed inline, beside the `applied` clauses `screen` already reports.
    """
    from qanat.filters import FilterError, clauses_of

    given = clauses not in (None, [], ())
    if filter and given:
        raise ScreenError(
            "give either `filter` or `clauses`, not both. To change a saved filter's "
            "thresholds for one screen, send its clauses changed; to change it for "
            "good, save it again")
    if not filter and not given:
        raise ScreenError(
            "a screen needs `clauses` -- [{metric, op, value}] -- or the `filter` id "
            "of a saved one. Calling it with neither would answer a question nobody "
            "asked")
    if filter:
        try:
            raw = clauses_of(project, filter)
        except FilterError as exc:
            raise ScreenError(str(exc)) from exc
    else:
        raw = clauses
    out = screen(store, project, parse_clauses(raw), **kw)
    return {"filter": filter or None, **out}


def _inputs(store: Store, project: Project, resolved: dict[str, int],
            stops: list[str], as_of: str | None, frm: str | None,
            to: str | None, every: str,
            picked: Membership | None = None) -> dict[str, Any]:
    """What this answer rested on, in a form a caller can store and compare.

    **`id` is a digest of these inputs, not a random handle**, and that is the
    useful choice rather than the conventional one. Two screens of the same
    filter over the same definitions and the same corpus share an id; a
    different id means an input moved. **A record that cannot tell those apart
    is not a record** -- an as-of date protects a replay from a filer restating
    their numbers, and it does not protect it from a parser being fixed and the
    corpus reloaded.

    There is no wall-clock timestamp in here on purpose. The same screen twice
    is the same answer, which is a claim this product makes and a test holds,
    and when a caller asked is the caller's own fact to record.

    **It was called `execution` until 0.11.0, and the word had to move.** An
    execution is one attempt at one job, which the job model names and the store
    records; this is the inputs behind one answer, which is a different thing
    entirely. Two meanings for one word is the state `source` was in, and the
    reason it was retired.
    """
    # A list of pairs rather than a mapping, because a filter may read two
    # versions of one definition and `{"pe": 1, "pe": 3}` cannot say that.
    used = sorted({(col.partition("@")[0], v) for col, v in resolved.items()})
    have = coverage(store, project, sorted({mid for mid, _v in used}))
    body = {
        "metrics": [{"metric": mid, "version": v} for mid, v in used],
        "dates": list(stops),
        "every": every if not as_of else None,
        "corpus": {"from": have["from"], "to": have["to"]},
        # The universe is one of the inputs, so it is part of the id: the same
        # filter over a different list is a different answer.
        "companies": None if picked is None else {
            "subjects": sorted({str(s) for s in picked.members["subject"]}),
            "point_in_time": picked.point_in_time,
        },
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {
        "id": digest,
        "as_of": as_of,
        "from": frm,
        "to": to,
        **body,
        "note": (
            "`id` is a digest of the definitions, the dates and the corpus range this "
            "answer rested on -- not a random handle. The same inputs give the same id, "
            "so an id that changes means something underneath moved. Store it beside "
            "whatever you keep: nothing here keeps it for you"
        ),
    }



__all__ = [
    "COLUMNS",
    "OPERATORS",
    "Clause",
    "Membership",
    "ScreenError",
    "coverage",
    "forward_returns",
    "metric_values",
    "parse_clauses",
    "read_membership",
    "run",
    "screen",
]
