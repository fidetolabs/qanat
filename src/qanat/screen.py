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
"""

from __future__ import annotations

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
    """

    metric: str
    op: str
    value: float

    def __post_init__(self) -> None:
        if self.op not in OPERATORS:
            raise ScreenError(
                f"no comparison called '{self.op}'. The ones there are: "
                + ", ".join(sorted(OPERATORS))
            )

    def test(self, series: pd.Series) -> pd.Series:
        return OPERATORS[self.op](series, self.value)

    def __str__(self) -> str:
        return f"{self.metric} {self.op} {self.value:g}"


@dataclass
class Filter:
    """A named rule holding several clauses. All of them have to pass."""

    name: str | None = None
    version: int | None = None
    clauses: list[Clause] = field(default_factory=list)

    def as_applied(self) -> dict[str, Any]:
        """What a caller records about this filter, as it ran.

        Name and version may both be absent. A clause somebody typed once and
        never saved has nothing to point at, and recording it as data rather
        than as a pointer is the only way the run stays readable afterwards.
        """
        return {
            "filter": self.name,
            "version": self.version,
            "clauses": [{"metric": c.metric, "op": c.op, "value": c.value}
                        for c in self.clauses],
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
    try:
        return Clause(metric=str(c["metric"]), op=str(c["op"]), value=float(c["value"]))
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
) -> dict[str, Any]:
    """Who passed, on one date or on every date in a window.

    Returns the three things a screen may report, and nothing else:

    | `rows`     | one row per subject per date, with whether it passed |
    | `counts`   | per date: how many could be evaluated, how many could not |
    | `applied`  | the filters as they ran, clauses and all |

    No average across dates, no comparison against the companies that failed,
    no score. A number that cannot be read off one date's list is not here.
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

    wanted = sorted({c.metric for f in filters for c in f.clauses})
    unknown = [m for m in wanted if project.metric(m) is None]
    if unknown:
        known = ", ".join(m.id for m in project.metrics) or "(none defined)"
        raise ScreenError(
            f"no metric called {', '.join(unknown)}. This project has: {known}"
        )

    rows: list[dict] = []
    counts: list[dict] = []
    for stop in stops:
        values = {m: metric_values(store, project.metric(m), stop) for m in wanted}
        subjects = sorted({s for df in values.values() for s in df["subject"]})
        table = pd.DataFrame({"subject": subjects})
        for m, df in values.items():
            table = table.merge(df.rename(columns={"value": m}), on="subject", how="left")

        # A subject missing any metric the filter names cannot be judged. It is
        # not a fail. Saying so is the count, and the count is why a list is a
        # claim rather than a rumour.
        evaluable = table[wanted].notna().all(axis=1) if wanted else pd.Series(
            True, index=table.index)

        passed = pd.Series(True, index=table.index)
        failed_on: list[list[str]] = [[] for _ in range(len(table))]
        for f in filters:
            for c in f.clauses:
                ok = c.test(table[c.metric])
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
            })
        counts.append({
            "as_of": stop,
            "subjects": len(table),
            "evaluated": int(evaluable.sum()),
            "dropped": int((~evaluable).sum()),
            "passed": int((passed & evaluable).sum()),
        })

    # The second of the three outputs. Absent when the project does not say
    # where a price lives, because a screen is still a screen without one.
    _attach_returns(rows, forward_returns(store, project))

    return {
        "rows": rows,
        "counts": counts,
        "applied": [f.as_applied() for f in filters],
    }


__all__ = [
    "COLUMNS",
    "OPERATORS",
    "Clause",
    "Filter",
    "ScreenError",
    "forward_returns",
    "metric_values",
    "parse_filters",
    "screen",
]
