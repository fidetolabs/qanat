"""An HTTP endpoint that returns JSON.

    - id: px
      stage: raw
      table: price_1d
      connector: rest
      schedule: "*/5 * * * *"
      options:
        url: https://api.example.com/v1/prices
        params: { interval: 1d }
        headers: { Authorization: "Bearer ${PRICE_API_KEY}" }
        records: data.items        # dot path to the list; omit if the body is a list
        orient: index              # the body is {key: {...}} rather than a list
        index_column: date         # what that key is called once it becomes a column
        rename: { t: ts, s: symbol, c: close }

`${VAR}` anywhere in url, params or headers is read from the environment, so a
key never has to be written into the file.

One request per symbol, stacked into one table. `{symbol}` is replaced in the
url, the params and the headers, and the symbol is kept as a column:

      symbols: [AAPL, MSFT]        # or from_universe: ./universes/sp100.csv
      url: https://api.example.com/v1/chart/{symbol}

Some APIs answer in a shape no combination of the options above can flatten --
columns held as parallel arrays, or several lists that have to be zipped. Rather
than teach this connector every one of them, land the body untouched and take it
apart in a step, which is what `raw` is for:

      payload: true

That writes one row per request: `fetched_at`, `source_id`, `symbol`, and
`payload`, the response as JSON text. Use a `.py` step to parse it -- DuckDB and
Postgres do not spell their JSON functions the same way.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from qanat.models import Source

_ENV = re.compile(r"\$\{([A-Z0-9_]+)\}")


def expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def dig(body: Any, path: str | None) -> Any:
    if not path:
        return body
    for part in path.split("."):
        body = body[part]
    return body


def symbols_of(source: Source, root: Path) -> list[str] | None:
    """The symbols to ask for one at a time, or None to make a single request."""
    o = source.options
    if syms := o.get("symbols"):
        return [str(s) for s in syms]
    if path := o.get("from_universe"):
        return [str(s) for s in pd.read_csv(root / path)["symbol"].tolist()]
    return None


def for_symbol(value: Any, symbol: str) -> Any:
    """Put `symbol` wherever the options wrote `{symbol}`."""
    if isinstance(value, str):
        return value.replace("{symbol}", symbol)
    if isinstance(value, dict):
        return {k: for_symbol(v, symbol) for k, v in value.items()}
    if isinstance(value, list):
        return [for_symbol(v, symbol) for v in value]
    return value


def _body(source: Source, symbol: str | None) -> Any:
    o = source.options
    url = expand(o.get("url"))
    if not url:
        raise ValueError(f"source '{source.id}': rest needs options.url")
    params, headers = expand(o.get("params")), expand(o.get("headers"))
    if symbol is not None:
        url, params, headers = (for_symbol(x, symbol) for x in (url, params, headers))
    r = httpx.request(
        o.get("method", "GET"),
        url,
        params=params or None,
        headers=headers or None,
        timeout=float(o.get("timeout", 30)),
    )
    r.raise_for_status()
    return r.json()


def _flatten(body: Any, o: dict[str, Any]) -> pd.DataFrame:
    records = dig(body, o.get("records"))
    if o.get("orient") == "index":
        # {"2024-01-02": {"EUR": 0.91, ...}, ...} -- one row per key, key kept as a column
        df = pd.DataFrame.from_dict(records, orient="index").sort_index()
        df.index.name = o.get("index_column", "key")
        df = df.reset_index()
    else:
        df = pd.DataFrame(records)
    if rename := o.get("rename"):
        df = df.rename(columns=rename)
    if keep := o.get("columns"):
        df = df[[c for c in keep if c in df.columns]]
    return df


def _envelope(source: Source, body: Any, symbol: str | None) -> pd.DataFrame:
    """The response as it arrived, with just enough around it to be a table.

    `fetched_at` is not decoration. Retention drops rows by time column, and a
    replay hides rows newer than the as-of date the same way, so a table of bare
    payloads would quietly opt out of both.
    """
    return pd.DataFrame([{
        "fetched_at": datetime.now(timezone.utc),
        "source_id": source.id,
        "symbol": symbol,
        "payload": json.dumps(body),
    }])


def fetch(source: Source, root: Path) -> pd.DataFrame:
    o = source.options
    shape = _envelope if o.get("payload") else None

    syms = symbols_of(source, root)
    if syms is None:
        body = _body(source, None)
        return shape(source, body, None) if shape else _flatten(body, o)

    frames = []
    for sym in syms:
        body = _body(source, sym)
        if shape:
            frames.append(shape(source, body, sym))
        else:
            part = _flatten(body, o)
            if "symbol" not in part.columns:
                part.insert(0, "symbol", sym)
            frames.append(part)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
