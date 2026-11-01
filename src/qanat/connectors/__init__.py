"""Connector adapters: how an ingestion job reaches outside the project.

Add one by writing `fetch(job, root) -> DataFrame` and registering it in
ADAPTERS. That is the whole plugin surface.

**This package was `qanat/sources/`, and the word is why it moved.** `source`
meant a declaration in the config, a module in here, the `connector:` value, and
a provenance string on every row. Four things, one word. The row keeps it; this
is named for what it actually is -- the thing that knows how to talk to one kind
of upstream.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from qanat.connectors import csv_connector, rest, sec, sql, yfinance_connector
from qanat.models import IngestionJobDefinition

ADAPTERS: dict[str, Callable[..., pd.DataFrame]] = {
    "rest": rest.fetch,
    "sql": sql.fetch,
    "csv": csv_connector.fetch,
    "sec": sec.fetch,
    "yfinance": yfinance_connector.fetch,
}


def fetch(job: IngestionJobDefinition, root: Path, on_warn=None,
          since: str | None = None) -> pd.DataFrame:
    """Pull one ingestion job's selection.

    `since` is the newest row already stored for this table, when the caller
    knows it. An adapter that can ask its upstream for only what came after
    that takes it; the rest ignore it. Passing it is what turns a definition
    that re-downloads its whole history on every poll into one that closes the
    gap the last poll left.
    """
    try:
        adapter = ADAPTERS[job.connector]
    except KeyError:
        raise KeyError(
            f"unknown connector '{job.connector}'. known: {', '.join(sorted(ADAPTERS))}"
        ) from None
    if getattr(adapter, "wants_since", False):
        return adapter(job, root, on_warn=on_warn, since=since)
    return adapter(job, root, on_warn=on_warn)


__all__ = ["ADAPTERS", "fetch"]
