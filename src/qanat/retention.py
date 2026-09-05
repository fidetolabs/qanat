"""Per-table row retention. Drop rows whose time column is older than the policy."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from qanat.models import Project
from qanat.store import Store, phys

_DURATION = re.compile(r"^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|"
                       r"h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$", re.IGNORECASE)

_TIME_COLS = ("ts", "timestamp", "time", "date", "as_of", "datetime", "fetched_at",
              "created_at", "updated_at")

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}


def parse_duration(text: str) -> timedelta:
    """Parse '7d', '24h', '30 minutes' into a timedelta."""
    raw = str(text).strip()
    m = _DURATION.match(raw)
    if not m:
        raise ValueError(f"retention must look like '7d' or '24h', got {text!r}")
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


def apply_retention(store: Store, ref: str, policy: str) -> int:
    """Delete rows older than `policy`. Returns rows removed."""
    if not store.exists(ref):
        return 0
    col = time_column(store, ref)
    if col is None:
        store.event("warn", "retention", f"{ref}: no time column, skipped")
        return 0
    delta = parse_duration(policy)
    cutoff = datetime.now(timezone.utc) - delta
    name = phys(ref)
    with store._lock:
        before = store.con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        store.con.execute(
            f'DELETE FROM "{name}" WHERE CAST("{col}" AS TIMESTAMP) < ?',
            [cutoff.replace(tzinfo=None)],
        )
        after = store.con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
    removed = int(before - after)
    if removed:
        store.event("info", "retention", f"{ref}: removed {removed:,} row(s) older than {policy}")
    return removed


def run_retention(store: Store, project: Project) -> dict[str, int]:
    """Apply every retention policy in the project file."""
    out: dict[str, int] = {}
    for ref, policy in project.retention.items():
        out[ref] = apply_retention(store, ref, policy)
    return out
