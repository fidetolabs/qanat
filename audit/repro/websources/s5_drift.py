"""Real-source failure modes: schema drift between polls, and the escape hatch."""
import shutil, pathlib
from qanat.models import Source
from qanat.sources import fetch
from qanat.store import Store

ROOT = pathlib.Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/weblab/drift")
if ROOT.exists(): shutil.rmtree(ROOT)
(ROOT / "data").mkdir(parents=True)
s = Store(ROOT / "data/q.duckdb")

def poll(sid, url, mode="append", key=None, **opts):
    src = Source.model_validate(dict(id=sid, to=[f"raw.{sid}"], connector="rest",
                                     mode=mode, key=key or [],
                                     options={"url": url, **opts}))
    try:
        df = fetch(src, ROOT)
        n = s.write(src.ref, df, mode=src.mode, key=src.key)
        return f"ok  {n} rows", None
    except Exception as exc:
        return "FAIL", f"{type(exc).__name__}: {str(exc).splitlines()[0][:88]}"

print("=" * 94)
print("A · same feed, two countries. KR has no 'counties'; US does.")
print("=" * 94)
r, e = poll("hol", "https://date.nager.at/api/v3/PublicHolidays/2024/KR")
print(f"  poll 1 (KR): {r} {e or ''}")
print(f"     counties column typed: {dict(s.table_info('raw.hol').columns)['counties']}")
r, e = poll("hol", "https://date.nager.at/api/v3/PublicHolidays/2024/US")
print(f"  poll 2 (US): {r} {e or ''}")
print(f"     table rows now: {s.table_info('raw.hol').rows}")
print("  -> a column that was all-null in the first response fixes the type forever")

print()
print("=" * 94)
print("B · the same live feed polled twice, append mode, no key (the default)")
print("=" * 94)
for i in (1, 2, 3):
    r, e = poll("fx", "https://api.frankfurter.dev/v1/2024-01-01..2024-01-31",
                params={"base": "EUR", "symbols": "USD"},
                records="rates", orient="index", index_column="date")
    print(f"  poll {i}: {r}  -> table holds {s.table_info('raw.fx').rows} rows")
print("  -> the ECB published 22 rates in January. After three polls the table says otherwise.")
print("     No warning at poll time, and `qanat check` has no rule about this.")

print()
print("=" * 94)
print("C · the two sources that would not land at all -- does the escape hatch save them?")
print("=" * 94)
for label, sid, url, opts in [
    ("Coinbase spot (a single dict)", "btc", "https://api.coinbase.com/v2/prices/BTC-USD/spot", {"records": "data"}),
    ("World Bank ([meta, rows])", "wb",
     "https://api.worldbank.org/v2/country/KR/indicator/NY.GDP.MKTP.CD", {"params": {"format": "json"}}),
]:
    r, e = poll(sid, url, mode="replace", **opts)
    print(f"  {label:<34} documented options -> {r} {e or ''}")
    r2, e2 = poll(sid + "_p", url, mode="replace", payload=True,
                  **({"params": opts["params"]} if "params" in opts else {}))
    print(f"  {'':<34} payload: true       -> {r2} {e2 or ''}")
    if r2.startswith("ok"):
        df = s.read(f"raw.{sid}_p")
        print(f"  {'':<34}   landed columns: {list(df.columns)}, payload is {len(df['payload'].iloc[0])} chars of JSON")
print()
print("  -> `payload: true` does rescue both, but it is not what the error message tells you,")
print("     and the docs only mention it for 'columns held as parallel arrays'.")
s.close()
