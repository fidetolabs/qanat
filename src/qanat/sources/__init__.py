"""Source adapters: how a stage gets fed from outside.

Add one by writing `fetch(source, root) -> DataFrame` and registering it in
ADAPTERS. That is the whole plugin surface.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd

from qanat.models import Source
from qanat.sources import csv_source, rest, sql, synthetic

ADAPTERS: dict[str, Callable[[Source, Path], pd.DataFrame]] = {
    "rest": rest.fetch,
    "sql": sql.fetch,
    "csv": csv_source.fetch,
    "synthetic": synthetic.fetch,
}


def fetch(source: Source, root: Path) -> pd.DataFrame:
    try:
        adapter = ADAPTERS[source.connector]
    except KeyError:
        raise KeyError(
            f"unknown connector '{source.connector}'. known: {', '.join(sorted(ADAPTERS))}"
        ) from None
    return adapter(source, root)


__all__ = ["ADAPTERS", "fetch"]
