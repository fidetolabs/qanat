"""What actually landed? Unicode, nulls, list columns, structs, and the clock."""
import pathlib
from qanat.store import Store
ROOT = pathlib.Path("/private/tmp/claude-501/-Users-seungwonsong-orca-qanat/b14f6a56-e880-47ee-abed-48e5bfd880d7/scratchpad/weblab/proj")
s = Store(ROOT / "data/q.duckdb")

print("=" * 92)
print("UNICODE + odd types · raw.holidays (Korean names, a null column, a list column)")
print("=" * 92)
df = s.read("raw.holidays")
print(df[["date", "localName", "name", "counties", "launchYear", "types"]].head(4).to_string(index=False))
print()
print("  column types:", {c: t for c, t in s.table_info("raw.holidays").columns})
print(f"  Korean text intact? {df['localName'].iloc[0]!r}")
print(f"  'types' (a JSON list) landed as: {df['types'].iloc[0]!r}  -> {type(df['types'].iloc[0]).__name__}")
print(f"  all-null 'counties' landed as: {s.table_info('raw.holidays').columns}"[:0] or
      f"  all-null 'counties' type: {dict(s.table_info('raw.holidays').columns)['counties']}")

print()
print("=" * 92)
print("THE CLOCK · which time column does qanat find on each table?")
print("=" * 92)
for ref in sorted(s.all_tables()):
    col = s.time_column(ref)
    mx = s.max_time(ref) if col else None
    cols = [c for c, _ in s.table_info(ref).columns]
    print(f"  {ref:<16} time_column={str(col):<10} max_time={str(mx):<26} cols={cols[:4]}")
print()
print("  raw.quakes has properties.time (epoch ms) nested inside a STRUCT.")
print("  qanat only looks at top-level column names, so it finds no clock at all:")
print("   -> as-of views cannot filter it, and retention cannot expire it.")

print()
print("=" * 92)
print("EPOCH MILLISECONDS · what happens if you do give qanat that column?")
print("=" * 92)
s.query("CREATE OR REPLACE TABLE raw__quakes_flat AS "
        "SELECT id, properties.time AS time, properties.mag AS mag, properties.place AS place "
        "FROM raw__quakes")
info = s.table_info("raw.quakes_flat")
print("  flattened:", [(c, t) for c, t in info.columns])
print("  time_column found:", s.time_column("raw.quakes_flat"))
raw = s.query("SELECT time FROM raw__quakes_flat LIMIT 1")["time"][0]
cast = s.query("SELECT CAST(time AS TIMESTAMP) AS t FROM raw__quakes_flat LIMIT 1")["t"][0]
print(f"  raw value        : {raw}   (epoch milliseconds = 2026-09-09)")
print(f"  what qanat's CAST makes of it: {cast}")
print(f"  max_time reports : {s.max_time('raw.quakes_flat')}")
s.close()
