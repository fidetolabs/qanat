"""Per-table row retention. Drop rows whose time column is older than the policy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from qanat.models import Project
from qanat.store import Store

_DURATION = re.compile(r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
                       r"h|hr|hrs|hour|hours|d|day|days|w|week|weeks|y|yr|yrs|year|years)$",
                       re.IGNORECASE)

_TIME_COLS = ("ts", "timestamp", "time", "date", "as_of", "datetime", "fetched_at",
              "created_at", "updated_at")

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
    # A year is 365 days here, not a calendar year. A screener that stops once
    # a year needs the unit, and the drift against a real calendar is four
    # days over five years, which is inside the gap between a fiscal year end
    # and the filing that reports it.
    "y": 31_536_000, "yr": 31_536_000, "yrs": 31_536_000,
    "year": 31_536_000, "years": 31_536_000,
}


def parse_duration(text: str, field: str = "retention") -> timedelta:
    """Parse '7d', '24h', '1y', '30 minutes' into a timedelta.

    `field` is the name to put in the error. It is shared with the screen's
    `every`, and it used to say "retention must look like '7d'" whichever one you
    had actually mistyped -- sending the reader to the wrong setting.
    """
    raw = str(text).strip()
    m = _DURATION.match(raw)
    if not m:
        raise ValueError(f"{field} must look like '7d' or '24h', got {text!r}")
    n, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(seconds=n * _UNITS[unit])


def time_column(store: Store, ref: str) -> str | None:
    info = store.table_info(ref)
    if not info:
        return None
    lower = {c.lower(): c for c, _ in info.columns}
    for cand in _TIME_COLS:
        if cand in lower:
            return lower[cand]
    return None


#: The shortest policy that is allowed. A retention of "1s" is one keystroke away
#: from "1d" and empties the table sixty seconds after it is saved.
MIN_RETENTION = timedelta(hours=1)


def _cutoff(store: Store, ref: str, policy: str) -> tuple[str, Any] | None:
    """The column and the instant a policy cuts at, or None if it cannot cut."""
    if not store.exists(ref):
        return None
    col = time_column(store, ref)
    if col is None:
        return None
    delta = parse_duration(policy)
    return col, (datetime.now(timezone.utc) - delta).replace(tzinfo=None)


def would_remove(store: Store, project: Project) -> list[dict[str, Any]]:
    """What every retention policy would remove right now, and nothing removed.

    **Said before it is done, because removing is the one thing that cannot be
    undone.** The policies used to be applied by the scheduler once a minute, so
    the first a person heard of a row going was an event in a log nobody was
    reading. The same numbers are now the answer to a question.
    """
    out: list[dict[str, Any]] = []
    for ref, policy in project.retention.items():
        cut = _cutoff(store, ref, policy)
        if cut is None:
            out.append({"table": ref, "policy": policy, "rows": 0,
                        "note": "nothing to do: no rows yet, or no time column to cut on"})
            continue
        col, cutoff = cut
        out.append({"table": ref, "policy": policy, "column": col,
                    "older_than": str(cutoff),
                    "rows": store.count_older(ref, col, cutoff)})
    return out


def apply_retention(store: Store, ref: str, policy: str) -> int:
    """Delete rows older than `policy`. Returns rows removed."""
    cut = _cutoff(store, ref, policy)
    if cut is None:
        store.event("warn", "retention", f"{ref}: nothing to cut on, skipped")
        return 0
    col, cutoff = cut
    removed = store.prune_rows(ref, col, cutoff)
    if removed:
        store.event("info", "retention", f"{ref}: removed {removed:,} row(s) older than {policy}")
    return removed


def run_retention(store: Store, project: Project) -> dict[str, int]:
    """Apply every retention policy in the project file.

    **Nothing calls this on a clock.** It is reached by a person asking for it --
    the `plan` tool's `prune` flag -- and `would_remove` is what they see first.
    """
    out: dict[str, int] = {}
    for ref, policy in project.retention.items():
        out[ref] = apply_retention(store, ref, policy)
    return out
