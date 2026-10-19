"""The as-of dates a screen stops at.

A screen over a window runs the pipeline once per date in it. This decides
which dates those are, and refuses windows that would run the whole graph more
times than anybody meant.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd

from qanat.retention import parse_duration


class WindowError(Exception):
    pass


#: How many as-of dates one screen may walk. Each one runs the whole upstream
#: pipeline, so this counts pipeline passes, not rows. A year at `1s` is 31.6M
#: of them: 49 seconds and 762 MB just to build the list, before a step ran.
MAX_STOPS = 20_000


def window(frm: str, to: str, every: str = "1y",
           limit: int | None = MAX_STOPS) -> list[str]:
    """The dates a screen stops at, first to last, both ends included."""
    if not frm or not to:
        raise WindowError("a window needs both `from` and `to`")
    try:
        step = parse_duration(every, "every")
    except ValueError as exc:
        raise WindowError(str(exc)) from exc
    if step <= timedelta(0):
        raise WindowError(f"`every` must be a positive duration, got {every!r}")
    start, end = pd.Timestamp(frm), pd.Timestamp(to)
    if end < start:
        raise WindowError(f"'to' ({to}) is before 'from' ({frm})")
    # Count first. Building the list to find out how big it is was the expensive part.
    n = int((end - start) / step) + 1
    if limit is not None and n > limit:
        raise WindowError(
            f"{frm}..{to} every {every} is {n:,} dates, and each one runs the whole "
            f"pipeline. The limit is {limit:,} -- widen the gap, or shorten the window"
        )
    out, cur = [], start
    while cur <= end:
        out.append(str(cur.date()))
        cur = cur + step
    return out


def next_stop(frm: str, after: str, every: str) -> str:
    """The first date on the grid that falls strictly after `after`.

    The grid is anchored on `frm`, so a run that resumes lands on the same
    dates a single pass over the whole window would have used.
    """
    step = parse_duration(every)
    cur = pd.Timestamp(frm)
    last = pd.Timestamp(after)
    if cur > last:
        return str(cur.date())
    n = int((last - cur) / step) + 1
    return str((cur + step * n).date())


__all__ = ["MAX_STOPS", "WindowError", "next_stop", "window"]
