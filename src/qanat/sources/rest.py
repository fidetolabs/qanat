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

`records` is a dot path, and a step in it that is a number indexes a list. The
World Bank answers `[{metadata}, [rows]]`, so:

      records: "1"                 # take the second element, which is the rows

A body that is one object rather than a list of them lands as a single row --
"the current price of BTC" needs no special option.

Some APIs still answer in a shape no combination of the above can flatten: several
lists that have to be zipped together, or records nested under keys that vary.
Rather than teach this connector every one of them, land the body untouched and
take it apart in a step, which is what `raw` is for:

      payload: true

That writes one row per request: `fetched_at`, `source_id`, `symbol`, and
`payload`, the response as JSON text. Use a `.py` step to parse it -- DuckDB and
Postgres do not spell their JSON functions the same way.

Columns held as parallel arrays -- `{"time": [...], "temp": [...]}` -- do *not*
need this. `records:` pointed at that object flattens it into rows already.
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

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class MissingEnv(ValueError):
    """A `${VAR}` in the options that the environment does not set."""


def expand(value: Any, missing: set[str] | None = None) -> Any:
    """Replace `${NAME}` from the environment.

    An unset name used to become an empty string, which produced `Illegal header
    value b'Bearer '`, or a request sent with a blank API key, or the actively wrong
    "rest needs options.url" when the option was set and the variable was not.
    Collect the names instead and let the caller say which one is missing.
    """
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name = m.group(1)
            if name not in os.environ:
                if missing is not None:
                    missing.add(name)
                return m.group(0)
            return os.environ[name]
        return _ENV.sub(sub, value)
    if isinstance(value, dict):
        return {k: expand(v, missing) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, missing) for v in value]
    return value


def dig(body: Any, path: str | None) -> Any:
    """Walk a dot path into the body. A numeric step indexes a list.

    The World Bank answers `[{metadata}, [rows]]`, and with a key-only path there
    was no way to say "the second element", so the body could not be landed at all.
    """
    if not path:
        return body
    for part in path.split("."):
        try:
            if isinstance(body, list):
                body = body[int(part)]
            else:
                body = body[part]
        except (KeyError, IndexError, TypeError, ValueError):
            where = (", ".join(map(str, body))[:120] if isinstance(body, dict)
                     else f"a list of {len(body)}" if isinstance(body, list)
                     else type(body).__name__)
            raise KeyError(
                f"records path '{path}' has no step '{part}'. At that point the body holds: "
                f"{where}"
            ) from None
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
    missing: set[str] = set()
    url = expand(o.get("url"), missing)
    params, headers = expand(o.get("params"), missing), expand(o.get("headers"), missing)
    if missing:
        raise MissingEnv(
            f"source '{source.id}': {', '.join('${' + m + '}' for m in sorted(missing))} "
            "is not set in the environment"
        )
    if not url:
        raise ValueError(f"source '{source.id}': rest needs options.url")
    if symbol is not None:
        url, params, headers = (for_symbol(x, symbol) for x in (url, params, headers))
    timeout = float(o.get("timeout", 30))
    try:
        r = httpx.request(
            o.get("method", "GET"), url,
            params=params or None, headers=headers or None, timeout=timeout,
        )
    except httpx.TimeoutException as exc:
        raise TimeoutError(
            f"source '{source.id}': {url} did not answer within {timeout:g}s. "
            "Set options.timeout higher if the endpoint is simply slow"
        ) from exc
    r.raise_for_status()
    try:
        return r.json()
    except ValueError as exc:
        ct = r.headers.get("content-type", "unknown")
        raise ValueError(
            f"source '{source.id}': {url} answered with {ct}, not JSON. "
            f"It starts: {r.text[:80]!r}"
        ) from exc


def _flatten(body: Any, o: dict[str, Any]) -> pd.DataFrame:
    records = dig(body, o.get("records"))
    if o.get("orient") == "index":
        # {"2024-01-02": {"EUR": 0.91, ...}, ...} -- one row per key, key kept as a column
        df = pd.DataFrame.from_dict(records, orient="index").sort_index()
        df.index.name = o.get("index_column", "key")
        df = df.reset_index()
    elif isinstance(records, dict) and not any(
        isinstance(v, (list, dict)) for v in records.values()
    ):
        # one object, not a list of them -- "the current value of one thing", which
        # is most of the web. pandas refuses a dict of scalars without an index, so
        # this used to be un-landable with any combination of options.
        df = pd.DataFrame([records])
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
    df = pd.DataFrame([{
        # naive UTC, not an aware datetime: an aware one lands as TIMESTAMPTZ and
        # then renders in the session zone, so every as-of comparison was off by the
        # machine's offset. Right on a UTC server, nine hours wrong in Seoul.
        "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None),
        "source_id": source.id,
        "symbol": symbol,
        "payload": json.dumps(body),
    }])
    # text, even when it is None. Typed from a first poll that carried no symbols,
    # this column landed as INTEGER and every later poll failed to cast into it.
    return df.astype({"symbol": "string"})


def fetch(source: Source, root: Path, on_warn=None) -> pd.DataFrame:
    o = source.options
    shape = _envelope if o.get("payload") else None

    syms = symbols_of(source, root)
    if syms is None:
        body = _body(source, None)
        return shape(source, body, None) if shape else _flatten(body, o)

    # One symbol failing used to discard every symbol that had already worked, so a
    # single delisted ticker stalled the feed indefinitely -- every poll landing
    # nothing until somebody edited the list.
    frames, failed = [], []
    for sym in syms:
        try:
            body = _body(source, sym)
        except Exception as exc:  # noqa: BLE001 -- one symbol, not the batch
            failed.append(f"{sym} ({type(exc).__name__})")
            continue
        if shape:
            frames.append(shape(source, body, sym))
        else:
            part = _flatten(body, o)
            if "symbol" not in part.columns:
                part.insert(0, "symbol", sym)
            frames.append(part)
    if failed and on_warn:
        on_warn(f"{len(failed)} of {len(syms)} symbol(s) did not answer: "
                + ", ".join(failed[:5]) + (" …" if len(failed) > 5 else ""))
    if not frames:
        if failed:
            raise RuntimeError(
                f"source '{source.id}': every symbol failed -- " + ", ".join(failed[:5])
            )
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
