"""STEP 1 of the chain: enroll six real public data sources.

Each source is configured the way the rest.py docstring says to configure it.
No workarounds yet -- the point is to find out what a person hits on day one.
"""
import shutil, pathlib, textwrap
from qanat.models import Source
from qanat.sources import fetch
from qanat.store import Store

ROOT = pathlib.Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/weblab/proj")
if ROOT.exists(): shutil.rmtree(ROOT)
(ROOT / "data").mkdir(parents=True)
store = Store(ROOT / "data/q.duckdb")

SOURCES = [
  ("fx  · ECB rates via Frankfurter", dict(
     id="fx", to=["raw.fx"], connector="rest", mode="replace",
     options={"url": "https://api.frankfurter.dev/v1/2024-01-01..2024-06-30",
              "params": {"base": "EUR", "symbols": "USD,KRW,JPY"},
              "records": "rates", "orient": "index", "index_column": "date"})),
  ("holidays · Nager.Date KR (unicode)", dict(
     id="holidays", to=["raw.holidays"], connector="rest", mode="replace",
     options={"url": "https://date.nager.at/api/v3/PublicHolidays/2024/KR"})),
  ("weather · Open-Meteo (parallel arrays)", dict(
     id="weather", to=["raw.weather"], connector="rest", mode="replace",
     options={"url": "https://api.open-meteo.com/v1/forecast",
              "params": {"latitude": 37.57, "longitude": 126.98,
                         "daily": "temperature_2m_max", "forecast_days": 10},
              "records": "daily"})),
  ("quakes · USGS GeoJSON (nested)", dict(
     id="quakes", to=["raw.quakes"], connector="rest", mode="replace",
     options={"url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson",
              "records": "features"})),
  ("btc · Coinbase spot (a single dict)", dict(
     id="btc", to=["raw.btc"], connector="rest", mode="replace",
     options={"url": "https://api.coinbase.com/v2/prices/BTC-USD/spot", "records": "data"})),
  ("gdp · CSV over HTTP (562 KB)", dict(
     id="gdp", to=["raw.gdp"], connector="csv", mode="replace",
     options={"path": "https://raw.githubusercontent.com/datasets/gdp/main/data/gdp.csv"})),
  ("worldbank · slow API, default timeout", dict(
     id="worldbank", to=["raw.wb"], connector="rest", mode="replace",
     options={"url": "https://api.worldbank.org/v2/country/KR;US/indicator/NY.GDP.MKTP.CD",
              "params": {"format": "json", "per_page": 50}})),
]

print("=" * 96)
print("STEP 1 · ENROLL  ·  six real public sources, configured the way the docs describe")
print("=" * 96)
for label, cfg in SOURCES:
    s = Source.model_validate(cfg)
    try:
        df = fetch(s, ROOT)
        if df is None or df.empty:
            print(f"  {label:<40} EMPTY frame"); continue
        n = store.write(s.ref, df, mode=s.mode, key=s.key)
        info = store.table_info(s.ref)
        cols = ", ".join(f"{c}:{t}" for c, t in info.columns[:6])
        print(f"  {label:<40} ok   {n:>5} rows · {cols}{' …' if len(info.columns)>6 else ''}")
    except Exception as exc:
        msg = str(exc).splitlines()[0]
        print(f"  {label:<40} FAIL {type(exc).__name__}: {msg[:64]}")
print()
print("tables landed:", store.all_tables())
store.close()
