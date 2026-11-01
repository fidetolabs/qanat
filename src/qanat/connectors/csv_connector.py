"""A CSV file or URL.

    connector: csv
    options:
      path: ./seed/prices.csv        # relative to the project root, or an http(s) URL
      dtype: { symbol: str }         # keep 005930 from becoming 5930
      sep: ";"                       # anything pandas read_csv takes, from the list below
      encoding: cp949
      parse_dates: [date]

Without `dtype`, a zero-padded identifier is a number: KRX `005930` lands as
`5930`, HKEX `00700` as `700`, and the universe file -- which lists them as text --
then joins to nothing. `options` used to be ignored entirely by this connector, so
there was no way to say otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qanat.connectors.rest import expand
from qanat.models import IngestionJobDefinition

#: What may be handed to `pandas.read_csv`. A list rather than a pass-through, so a
#: misspelled option is an error the user sees instead of a setting silently ignored.
READ_CSV_OPTIONS = (
    "sep", "delimiter", "header", "names", "usecols", "dtype", "engine", "skiprows",
    "nrows", "na_values", "keep_default_na", "parse_dates", "date_format", "dayfirst",
    "thousands", "decimal", "comment", "encoding", "quotechar", "escapechar",
    "true_values", "false_values", "skipinitialspace", "compression",
)
_NOT_FOR_READ = {"path", "file"}


def read_options(job: IngestionJobDefinition) -> dict:
    """The `read_csv` keywords this ingestion job asked for, checked."""
    out, unknown = {}, []
    for k, v in (job.options or {}).items():
        if k in _NOT_FOR_READ:
            continue
        if k in READ_CSV_OPTIONS:
            out[k] = v
        else:
            unknown.append(k)
    if unknown:
        raise ValueError(
            f"ingestion job '{job.id}': csv does not know the option(s) "
            f"{', '.join(sorted(unknown))}. It takes path and any of: "
            f"{', '.join(sorted(READ_CSV_OPTIONS))}"
        )
    return out


def fetch(job: IngestionJobDefinition, root: Path, on_warn=None) -> pd.DataFrame:
    p = expand(job.options.get("path"))
    if not p:
        raise ValueError(f"ingestion job '{job.id}': csv needs options.path")
    kw = read_options(job)
    if str(p).startswith(("http://", "https://")):
        return pd.read_csv(p, **kw)
    return pd.read_csv(root / p, **kw)
