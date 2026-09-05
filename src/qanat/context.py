"""What a step script is handed when it runs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from qanat.models import Project, Step
from qanat.store import Store

#: what a universe file may call the day a symbol joined and the day it left
JOINED = ("from", "from_date", "start", "start_date", "added", "since")
LEFT = ("to", "to_date", "end", "end_date", "removed", "until")


def _col(df: pd.DataFrame, names: tuple[str, ...]) -> str | None:
    lower = {c.lower(): c for c in df.columns}
    return next((lower[n] for n in names if n in lower), None)


def is_point_in_time(df: pd.DataFrame) -> bool:
    """Does this universe file know when its members joined and left?"""
    return _col(df, JOINED) is not None or _col(df, LEFT) is not None


def members(df: pd.DataFrame, as_of: str | None) -> pd.DataFrame:
    """The universe on one day.

    A file with no dates is the same list on every day -- which is the survivorship
    bias, stated plainly: it is today's list, applied to the past.
    """
    joined, left = _col(df, JOINED), _col(df, LEFT)
    if as_of is None or (joined is None and left is None):
        return df
    day = pd.Timestamp(as_of)
    keep = pd.Series(True, index=df.index)
    if joined is not None:
        since = pd.to_datetime(df[joined], errors="coerce")
        keep &= since.isna() | (since <= day)
    if left is not None:
        until = pd.to_datetime(df[left], errors="coerce")
        keep &= until.isna() | (until > day)
    return df[keep].reset_index(drop=True)


class Context:
    """The one argument every Python step receives.

        def run(ctx):
            bars = ctx.read("normalized.prices_1d")
            return {"mom_20": ...}
    """

    def __init__(
        self,
        store: Store,
        project: Project,
        root: Path,
        step: Step,
        as_of: str | None = None,
        universe: str | None = None,
    ):
        self.store = store
        self.project = project
        self.root = root
        self.step = step
        self.options: dict[str, Any] = dict(step.options)
        #: Set when this step is being replayed. `ctx.read` will not return a row
        #: newer than this, and `ctx.now` is this instant rather than the wall clock,
        #: so the same replay twice gives the same numbers.
        self.as_of: str | None = as_of
        #: A backtest may hold the same alpha to a different universe than the file
        #: names, so "does this work on large caps too" costs one argument, not an edit.
        self.universe_override: str | None = universe
        self.now: datetime = (
            pd.Timestamp(as_of).to_pydatetime() if as_of else datetime.now(timezone.utc)
        )

    def read(self, ref: str, limit: int | None = None) -> pd.DataFrame:
        """Read a table this step declared in `reads`, as of the replay date if there is one."""
        if ref not in self.step.reads:
            raise KeyError(
                f"step '{self.step.id}' did not declare '{ref}' in reads -- "
                "add it so the lineage stays true"
            )
        return self.store.read(ref, limit, as_of=self.as_of)

    def sql(self, query: str) -> pd.DataFrame:
        """Run SQL over the store. Tables are named `stage__table`."""
        return self.store.query(query)

    def universe(self, bid: str | None = None, as_of: str | None = None) -> pd.DataFrame:
        """Which symbols this step is allowed to hold, **on the day it is deciding**.

        A universe file may carry `from` and `to` columns. When it does, this returns
        only the members of that date, so a replay never holds a name before it
        listed and never quietly drops one that was delisted. Without those columns
        the list is static, and every number built on it carries survivorship bias --
        `qanat check` says so.
        """
        bid = bid or self.universe_override or self.step.universe
        if not bid:
            raise ValueError(f"step '{self.step.id}' has no universe set")
        b = self.project.universe(bid)
        if b is None:
            raise KeyError(f"unknown universe '{bid}'")
        return members(pd.read_csv(self.root / b.symbols), as_of or self.as_of)

    def log(self, message: str, level: str = "info") -> None:
        self.store.event(level, self.step.id, str(message))
