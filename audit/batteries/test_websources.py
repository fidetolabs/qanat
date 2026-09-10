"""The shapes the public web actually answers in.

Every source here is real, free, and needs no key. They are in this battery because
synthetic fixtures never produce the things that broke the connector: a body that
is one object rather than a list, a list whose first element is metadata, a
timestamp in epoch milliseconds, a field that is null in one country and a list in
the next, a ticker with a leading zero.

    uv run pytest audit/batteries -m "audit and not network"    # skip these

Be a good guest: one call per endpoint, and no retries. If a host is down the test
skips rather than fails -- a broken third party is not a broken qanat.
"""

import pytest

from qanat.models import Source
from qanat.sources import fetch

pytestmark = pytest.mark.network


def _fetch(tmp_path, **cfg):
    src = Source.model_validate({"id": "probe", "to": ["raw.probe"], "connector": "rest",
                                 "mode": "replace", **cfg})
    try:
        return fetch(src, tmp_path)
    except Exception as exc:
        if any(w in str(exc).lower() for w in ("timeout", "connect", "temporarily", "503", "502")):
            pytest.skip(f"the host is not answering right now: {exc}")
        raise


def test_a_date_keyed_object_of_objects(tmp_path):
    """ECB reference rates. The `orient: index` case, on the real thing."""
    df = _fetch(tmp_path, options={
        "url": "https://api.frankfurter.dev/v1/2024-01-01..2024-01-31",
        "params": {"base": "EUR", "symbols": "USD,KRW"},
        "records": "rates", "orient": "index", "index_column": "date", "timeout": 30})
    assert len(df) > 15
    assert {"date", "USD", "KRW"} <= set(df.columns)


def test_a_plain_list_with_text_that_is_not_ascii(tmp_path):
    """Korean public holidays. Unicode has to survive the whole path, and this feed
    also carries a field that is null for Korea and a list for the United States."""
    df = _fetch(tmp_path, options={
        "url": "https://date.nager.at/api/v3/PublicHolidays/2024/KR", "timeout": 30})
    assert len(df) > 10
    assert any("가" <= ch <= "힣" for ch in "".join(map(str, df["localName"])))


def test_parallel_arrays_need_no_escape_hatch(tmp_path):
    """`{"time": [...], "temperature": [...]}`. The docstring used to offer
    `payload: true` for exactly this, and it was never needed."""
    df = _fetch(tmp_path, options={
        "url": "https://api.open-meteo.com/v1/forecast",
        "params": {"latitude": 37.57, "longitude": 126.98,
                   "daily": "temperature_2m_max", "forecast_days": 7},
        "records": "daily", "timeout": 30})
    assert len(df) == 7
    assert {"time", "temperature_2m_max"} <= set(df.columns)


def test_a_body_that_is_one_object_not_a_list(tmp_path):
    """"The current price of one thing" is most of the web, and it could not be
    landed with any combination of options."""
    df = _fetch(tmp_path, options={
        "url": "https://api.coinbase.com/v2/prices/BTC-USD/spot",
        "records": "data", "timeout": 30})
    assert len(df) == 1
    assert {"amount", "base", "currency"} <= set(df.columns)


def test_a_list_whose_first_element_is_metadata(tmp_path):
    """The World Bank answers `[{page: 1, ...}, [rows]]`, so `records:` needs an
    index step. It is also slow enough to need its own timeout."""
    df = _fetch(tmp_path, options={
        "url": "https://api.worldbank.org/v2/country/KR/indicator/NY.GDP.MKTP.CD",
        "params": {"format": "json", "per_page": 20},
        "records": "1", "timeout": 90})
    assert len(df) > 5
    assert "value" in df.columns


def test_nested_geojson_with_an_epoch_clock(tmp_path, monkeypatch):
    """USGS puts its timestamp at `properties.time`, in epoch milliseconds -- the
    format that used to raise `Unimplemented type for cast` on every as-of read."""
    import pandas as pd

    from qanat.store import Store

    df = _fetch(tmp_path, options={
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
        "records": "features", "timeout": 30})
    assert len(df) > 0

    store = Store(tmp_path / "q.duckdb")
    store.write("raw.quakes", df)
    flat = store.query("SELECT id, properties.time AS time FROM raw__quakes")
    store.write("raw.flat", flat)
    assert store.time_column("raw.flat") == "time"
    newest = store.max_time("raw.flat")
    assert pd.Timestamp(newest).year >= 2024, f"epoch ms read as {newest}"
    store.close()


def test_a_zero_padded_ticker_survives_a_csv_over_http(tmp_path):
    """Not a live feed -- a local file, because the point is the connector's
    options, and KRX codes are the case that made this matter."""
    (tmp_path / "k.csv").write_text("date,symbol,close\n2024-01-01,005930,71000\n")
    src = Source.model_validate({"id": "k", "to": ["raw.k"], "connector": "csv",
                                 "options": {"path": "./k.csv", "dtype": {"symbol": "str"}}})
    assert list(fetch(src, tmp_path)["symbol"]) == ["005930"]
