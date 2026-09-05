"""A database, read through SQLAlchemy.

    connector: sql
    options:
      dsn: postgresql+psycopg://user:pass@host/db     # or ${DATABASE_URL}
      query: SELECT ts, symbol, close FROM prices WHERE ts > now() - interval '1 day'

Install the extra:  pip install "qanat[sql]"  (plus your database driver).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qanat.models import Source
from qanat.sources.rest import expand


def fetch(source: Source, root: Path) -> pd.DataFrame:
    o = source.options
    dsn = expand(o.get("dsn"))
    query = o.get("query")
    if not dsn or not query:
        raise ValueError(f"source '{source.id}': sql needs options.dsn and options.query")
    try:
        from sqlalchemy import create_engine
    except ImportError:
        raise ImportError(
            "the sql source needs SQLAlchemy: pip install 'qanat[sql]'"
        ) from None
    engine = create_engine(dsn)
    with engine.connect() as con:
        return pd.read_sql(query, con)
