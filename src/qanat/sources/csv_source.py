"""A CSV file or URL.

    connector: csv
    options:
      path: ./seed/prices.csv        # relative to the project root, or an http(s) URL
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qanat.models import Source
from qanat.sources.rest import expand


def fetch(source: Source, root: Path) -> pd.DataFrame:
    p = expand(source.options.get("path"))
    if not p:
        raise ValueError(f"source '{source.id}': csv needs options.path")
    if str(p).startswith(("http://", "https://")):
        return pd.read_csv(p)
    return pd.read_csv(root / p)
