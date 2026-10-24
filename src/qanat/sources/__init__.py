"""Source adapters: how a stage gets fed from outside.

Add one by writing `fetch(source, root) -> DataFrame` and registering it in
ADAPTERS. That is the whole plugin surface.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from qanat.models import Source
from qanat.sources import csv_source, rest, sec, sql, yfinance_source

ADAPTERS: dict[str, Callable[..., pd.DataFrame]] = {
    "rest": rest.fetch,
    "sql": sql.fetch,
    "csv": csv_source.fetch,
    "sec": sec.fetch,
    "yfinance": yfinance_source.fetch,
}


def fetch(source: Source, root: Path, on_warn=None, since: str | None = None) -> pd.DataFrame:
    """Pull one source.

    `since` is the newest row already stored for this table, when the caller
    knows it. An adapter that can ask its upstream for only what came after
    that takes it; the rest ignore it. Passing it is what turns a source that
    re-downloads its whole history on every poll into one that closes the gap
    the last poll left.
    """
    try:
        adapter = ADAPTERS[source.connector]
    except KeyError:
        raise KeyError(
            f"unknown connector '{source.connector}'. known: {', '.join(sorted(ADAPTERS))}"
        ) from None
    if getattr(adapter, "wants_since", False):
        return adapter(source, root, on_warn=on_warn, since=since)
    return adapter(source, root, on_warn=on_warn)


__all__ = ["ADAPTERS", "fetch"]
