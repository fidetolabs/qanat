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

    screen(store, project, filters, as_of="2025-12-31")
    screen(store, project, filters, frm="2021-01-01", to="2025-12-31")

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
execution; the caller owns the filter, the history and the results.**

**Which puts an obligation on this end, and it is the whole of what makes the
split workable.** A caller that keeps its own history can only keep what it was
given. So a screen returns the companies, **the metric values behind the
verdict**, the definition versions it actually resolved, and an execution id --
enough for the caller to store a record it can read back later without asking
again. Returning a bare pass/fail would make the caller re-query for the numbers
it had just paid to compute.

What a screen does with a filter is accept the name and report it back:

    filters=[{"filter": "deep_value", "version": 2, "clauses": [
        {"metric": "pe", "version": 3, "op": "lt", "value": 15},
        {"metric": "net_margin", "op": "gte", "value": 0.2}]}]

`applied` comes back carrying that name and version, so whoever stored the
filter can record which rule produced which list. An unnamed list of clauses
still works, because trying a threshold before naming it is the normal way
round.

**A clause may pin the metric version it meant.** Without one it reads the
current definition, which is right for a fresh screen and wrong for a rule
written against version 3 when version 4 now exists. With one, bumping a metric
cannot silently change what a saved filter computes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from qanat.models import Metric, Project
from qanat.store import Store


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
    version: int | None = None

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise ScreenError(
                f"no comparison called '{self.op}'. The ones there are: "
                + ", ".join(sorted(OPERATORS))
            )

    @property
    def key(self) -> tuple[str, int | None]:
        """What identifies the column this clause reads: the metric at a version."""
        return (self.metric, self.version)

    @property
    def column(self) -> str:
        """The name this clause's values go under while the screen runs."""
        return self.metric if self.version is None else f"{self.metric}@{self.version}"

    def test(self, series: pd.Series) -> pd.Series:
        return OPERATORS[self.op](series, self.value)

    def __str__(self) -> str:
        at = "" if self.version is None else f"@{self.version}"
        return f"{self.metric}{at} {self.op} {self.value:g}"


@dataclass
class Filter:
    """A named rule holding several clauses. All of them have to pass."""

    name: str | None = None
    version: int | None = None
    clauses: list[Clause] = field(default_factory=list)

    def as_applied(self, resolved: dict[str, int] | None = None) -> dict[str, Any]:
        """What a caller records about this filter, as it ran.

        Name and version may both be absent. A clause somebody typed once and
        never saved has nothing to point at, and recording it as data rather
        than as a pointer is the only way the run stays readable afterwards.

        `resolved` carries the definition version each clause actually read.
        `metric_version` is what the caller pinned and may be absent;
        `metric_version_used` is what ran and never is.
        """
        resolved = resolved or {}
        return {
            "filter": self.name,
            "version": self.version,
            "clauses": [
                {"metric": c.metric, "op": c.op, "value": c.value,
                 **({} if c.version is None else {"metric_version": c.version}),
                 **({} if c.column not in resolved
                    else {"metric_version_used": resolved[c.column]})}
                for c in self.clauses
            ],
        }


def parse_filters(raw: Any) -> list[Filter]:
    """Accept the shapes a caller actually sends.

    A bare list of clauses is one unnamed filter, which is the common case and
    should not need wrapping.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ScreenError("filters must be a list")
    # A bare list of clauses, rather than a list of filters.
    if raw and all(isinstance(x, dict) and "metric" in x for x in raw):
        return [Filter(clauses=[_clause(x) for x in raw])]
    out = []
    for f in raw:
        if not isinstance(f, dict):
            raise ScreenError(f"a filter must be an object, got {type(f).__name__}")
        out.append(Filter(
            name=f.get("filter") or f.get("name"),
            version=f.get("version"),
            clauses=[_clause(c) for c in (f.get("clauses") or [])],
        ))
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
        return Clause(metric=str(c["metric"]), op=str(c["op"]), value=float(c["value"]),
                      version=None if pinned is None else int(pinned))
    except (TypeError, ValueError) as exc:
        raise ScreenError(f"clause value must be a number: {c}") from exc


# ------------------------------------------------------------------ the metrics
_AS_OF = re.compile(r":as_of\b")


def metric_values(store: Store, metric: Metric, as_of: str) -> pd.DataFrame:
    """One metric for every subject, as it stood on one date.

    The formula is the user's SQL. `:as_of` in it is replaced with the date, so
    a definition can filter its own history rather than relying on the caller
    to have opened a screen first.
    """
    sql = _AS_OF.sub(f"DATE '{as_of}'", metric.sql)
    try:
        df = store.query(sql)
    except Exception as exc:
        raise ScreenError(f"metric '{metric.id}' failed: {exc}") from exc
    cols = {c.lower(): c for c in df.columns}
    if "subject" not in cols or "value" not in cols:
        raise ScreenError(
            f"metric '{metric.id}' must select `subject` and `value`, got: "
            + ", ".join(df.columns)
        )
    out = df[[cols["subject"], cols["value"]]].copy()
    out.columns = ["subject", "value"]
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out


# ------------------------------------------------------------------ the universe
@dataclass
class Universe:
    """Which subjects a screen may return, and whether each name was checked.

    **A universe is the caller's list and this platform's execution.** Which
    companies somebody cares about is their business -- it is named on the
    client's side of the line, along with their filters and their history -- so
    this takes the list with the call and keeps none of it. **Placing those
    names against the filers this corpus holds, on the right date, is the part
    that is not their business**, and it is the whole of what this does.

    One form, deliberately. An earlier version also accepted the name of a
    universe declared in the project file, and that was the same mistake as
    storing a filter: it made this end look up a list belonging to the caller.
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


def read_universe(store: Store, project: Project,
                  universe: Sequence[Any]) -> Universe:
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

    if isinstance(universe, (str, bytes)):
        raise ScreenError(
            "universe is a list of names, not one name. Pass [\"AAPL\"] rather than "
            "\"AAPL\" -- and a list is all it takes, because which companies you care "
            "about is yours to keep, not this project's"
        )
    rows: list[dict[str, Any]] = []
    for entry in universe:
        if isinstance(entry, dict):
            symbol = str(entry.get("symbol") or entry.get("subject") or "").strip()
            if not symbol:
                raise ScreenError(f"a universe entry needs a `symbol`: {entry}")
            rows.append({"symbol": symbol, "from": entry.get("from"),
                         "to": entry.get("to")})
        else:
            rows.append({"symbol": str(entry).strip()})
    rows = [r for r in rows if r["symbol"]]
    if not rows:
        raise ScreenError("a universe needs at least one name")

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
    return Universe(
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
    from qanat.metrics import tables_read

    which = list(metrics) if metrics else project.metric_ids()
    refs: list[str] = []
    for mid in which:
        for version in project.metric_versions(mid):
            m = project.metric(mid, version)
            if m is not None:
                refs.extend(tables_read(project, m.sql))
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
            f"Ask for a window inside that, or call `coverage` first"
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
COLUMNS = ["as_of", "subject", "passed", "failed_on", "evaluated"]


def screen(
    store: Store,
    project: Project,
    filters: Sequence[Filter],
    as_of: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    every: str = "1y",
    universe: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Who passed, on one date or on every date in a window.

    Returns what a screen may report, and nothing else:

    | `rows`      | one row per subject per date, the verdict, and the values |
    | `counts`    | per date: how many could be evaluated, how many could not |
    | `applied`   | the filters as they ran, clauses and all |
    | `execution` | the definition versions it read, and the corpus it read them from |

    No average across dates, no comparison against the companies that failed,
    no score. A number that cannot be read off one date's list is not here.

    **`universe` bounds who is considered**, as a list of tickers or CIKs you
    pass with the call. **It widens the answer as well as narrowing it**: a name
    you asked about that has no value for a metric the filter names comes back as
    a row that could not be evaluated, rather than being absent. Without a
    universe there is no way to know who is missing -- the subjects are whoever
    the metrics returned -- and "nobody else qualified" and "I could not judge
    nine of them" are not the same answer.

    `list_companies` is what a caller builds that list from.
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
    if not stops:
        raise ScreenError("that window holds no dates")

    # One entry per metric *at a version*, because two clauses may read two
    # versions of one formula -- which is the whole reason a clause may pin one.
    wanted: dict[tuple[str, int | None], str] = {}
    for f in filters:
        for c in f.clauses:
            wanted[c.key] = c.column

    missing: list[str] = []
    for mid, version in wanted:
        if project.metric(mid, version) is None:
            if version is not None and project.metric(mid) is not None:
                have = ", ".join(map(str, project.metric_versions(mid)))
                missing.append(f"'{mid}' has no version {version} (it has {have})")
            else:
                missing.append(f"no metric called '{mid}'")
    if missing:
        known = ", ".join(project.metric_ids()) or "(none defined)"
        raise ScreenError(f"{'; '.join(missing)}. This project has: {known}")

    picked: Universe | None = None
    if universe is not None:
        picked = read_universe(store, project, universe)
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
                "this universe carries no join and leave dates, so it is today's "
                "membership applied to every date screened. Every number built on it is "
                "flattered by the names that survived"
            )

    # Which definition each clause actually read. An unpinned clause reads the
    # current version, so recording only what the caller pinned would leave a
    # kept answer unable to say what computed it -- and then it is not a record.
    resolved = {
        col: project.metric(mid, version).version
        for (mid, version), col in wanted.items()
    }

    columns = list(wanted.values())
    rows: list[dict] = []
    counts: list[dict] = []
    for stop in stops:
        values = {
            col: metric_values(store, project.metric(mid, version), stop)
            for (mid, version), col in wanted.items()
        }
        # Without a universe the subjects are whoever the metrics returned.
        # With one they are the membership on this date, so a member the
        # metrics say nothing about is still counted -- as unjudgeable.
        if picked is None:
            subjects = sorted({s for df in values.values() for s in df["subject"]})
        else:
            subjects = picked.on(stop)
        table = pd.DataFrame({"subject": subjects})
        for col, df in values.items():
            table = table.merge(df.rename(columns={"value": col}), on="subject", how="left")

        # A subject missing any metric the filter names cannot be judged. It is
        # not a fail. Saying so is the count, and the count is why a list is a
        # claim rather than a rumour.
        evaluable = table[columns].notna().all(axis=1) if columns else pd.Series(
            True, index=table.index)

        passed = pd.Series(True, index=table.index)
        failed_on: list[list[str]] = [[] for _ in range(len(table))]
        for f in filters:
            for c in f.clauses:
                ok = c.test(table[c.column])
                for i in table.index[~ok.fillna(False)]:
                    failed_on[i].append(str(c))
                passed &= ok.fillna(False)

        for i, subject in enumerate(table["subject"]):
            rows.append({
                "as_of": stop,
                "subject": subject,
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
        "applied": [f.as_applied(resolved) for f in filters],
        "execution": _execution(store, project, resolved, stops, as_of, frm, to, every,
                                picked),
    }
    if picked is not None:
        out["universe"] = picked.as_applied()
    if notes:
        out["notes"] = notes
    return out


def _execution(store: Store, project: Project, resolved: dict[str, int],
               stops: list[str], as_of: str | None, frm: str | None,
               to: str | None, every: str,
               picked: Universe | None = None) -> dict[str, Any]:
    """What this run was, in a form a caller can store and compare.

    **`id` is a digest of the inputs, not a random handle**, and that is the
    useful choice rather than the conventional one. Two runs of the same filter
    over the same definitions and the same corpus share an id; a different id
    means an input moved. **A record that cannot tell those apart is not a
    record** -- an as-of date protects a replay from a filer restating their
    numbers, and it does not protect it from a parser being fixed and the corpus
    reloaded.

    There is no wall-clock timestamp in here on purpose. The same screen twice
    is the same answer, which is a claim this product makes and a test holds,
    and when a caller asked is the caller's own fact to record.
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
        # The universe is part of what this run was, so it is part of the id:
        # the same filter over a different list is a different answer.
        "universe": None if picked is None else {
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
            "ran over -- not a random handle. The same inputs give the same id, so an "
            "id that changes means something underneath moved. Store it beside whatever "
            "you keep: nothing here keeps it for you"
        ),
    }



__all__ = [
    "COLUMNS",
    "OPERATORS",
    "Clause",
    "Filter",
    "ScreenError",
    "Universe",
    "coverage",
    "forward_returns",
    "metric_values",
    "parse_filters",
    "read_universe",
    "screen",
]
