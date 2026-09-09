"""C1/C2: identifier injection through a table name that nothing validates."""
import sys, tempfile, pathlib
import pandas as pd
from qanat.store import Store, phys

tmp = pathlib.Path(tempfile.mkdtemp())
s = Store(tmp / "t.duckdb")
s.write("raw.keepme", pd.DataFrame({"a": [1, 2, 3]}))
print("before:", s.all_tables())

# a table name that no validator ever looks at
evil = 'raw.x" AS SELECT 1; DROP TABLE "raw__keepme'
print("phys ->", phys(evil))
try:
    n = s.sql_into(evil, "SELECT 1 AS z")
    print("sql_into returned", n)
except Exception as exc:
    print("sql_into raised:", type(exc).__name__, str(exc)[:200])
print("after :", s.all_tables())
print("keepme still there:", s.exists("raw.keepme"))

# and through the write() path (pandas register + CREATE OR REPLACE)
try:
    s.write('raw.y" AS SELECT 1; DROP TABLE "raw__keepme', pd.DataFrame({"b": [1]}))
except Exception as exc:
    print("write raised:", type(exc).__name__, str(exc)[:200])
print("after write:", s.all_tables(), "keepme:", s.exists("raw.keepme"))
