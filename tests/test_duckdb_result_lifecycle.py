"""The store must never leave a DuckDB result open, and cleanup must never lie.

0.10.0 shipped with `duckdb>=1.0` and no upper bound, so `uv tool install`
resolved **2.0.0** while the lockfile pinned 1.5.5 and every CI job ran
`uv sync --frozen`. DuckDB 2.0 refuses `register()`/`unregister()` on a
connection holding an unfinished result, and a one-row fetch leaves exactly
that. Reads were untouched -- a read is nothing but `execute` -- and writes are
the only thing that registers a frame, so the product went read-only.

Two shapes came out of it, and the second is the one worth a test for ever:

* **A** -- the fresh store. `__init__` reads `current_schema()`, so `register`
  itself failed and nothing was written. A loud, honest failure.
* **B** -- preceding reads had drained the connection, so `register` succeeded,
  the parquet file landed, the view was rebuilt, and *then* a discarded
  `count(*)` poisoned the connection and `unregister` threw from the `finally`.
  **The write succeeded and the caller was told it failed.** `qanat run` said
  `0 rows, failed` while `qanat ls` showed the rows.

Shape B is a wrong answer, not an error, which is the class this suite exists to
rule out. So the test is not "the undrained read is gone" -- it is **cleanup
cannot fail a write that reached disk**, which holds no matter what a future
DuckDB tightens next.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import pytest

from qanat.store import Store

SRC = Path(__file__).resolve().parents[1] / "src" / "qanat"
# The method whose result is not drained. Spelled in two pieces so this file
# does not match its own grep.
ONE_ROW_FETCH = "fetch" + "one()"


def rows(n: int, start: int = 0) -> pd.DataFrame:
    return pd.DataFrame({
        "id": [i + start for i in range(n)],
        "sym": [f"S{(i + start) % 5}" for i in range(n)],
        "px": [float(i + start) for i in range(n)],
    })


@pytest.fixture(params=["parquet", "duckdb"])
def store(request, tmp_path: Path):
    path = tmp_path / "corpus" if request.param == "parquet" else tmp_path / "corpus.duckdb"
    s = Store(str(path))
    assert s.kind == request.param
    yield s
    s.close()


# --------------------------------------------------------------------- shape A
def test_a_fresh_store_writes_on_its_first_call(store: Store):
    """`__init__` reads the schema name. That read used to break this write."""
    assert store.write("raw.t", rows(2), mode="append", key=["id"]) == 2
    assert len(store.read("raw.t")) == 2


# --------------------------------------------------------------------- shape B
class _CleanupFails:
    """The connection, with `unregister` throwing -- what DuckDB 2.0 did.

    Faked rather than reproduced, deliberately: the point is not that *this*
    DuckDB raises there, it is that **nothing** raised there may turn a landed
    write into a reported failure. A version-specific reproduction would stop
    guarding the moment the version moved on.
    """

    def __init__(self, con):
        self._con = con

    def unregister(self, name):
        raise RuntimeError(f"cleanup refused to release {name}")

    def __getattr__(self, name):
        return getattr(self._con, name)


def test_a_write_that_reached_disk_is_never_reported_as_a_failure(store: Store):
    real = store.con
    store.con = _CleanupFails(real)
    try:
        landed = store.write("raw.t", rows(3), mode="append", key=["id"])
    finally:
        store.con = real
    assert landed == 3, "the rows are on disk; the caller must be told so"
    assert len(store.read("raw.t")) == 3


def test_cleanup_failing_does_not_break_the_write_after_it(store: Store):
    """One failed release must not cost the rest of the process its writes."""
    store.write("raw.t", rows(2), mode="append", key=["id"])
    real = store.con
    store.con = _CleanupFails(real)
    try:
        store.write("raw.t", rows(2, start=2), mode="append", key=["id"])
    finally:
        store.con = real
    assert store.write("raw.t", rows(2, start=4), mode="append", key=["id"]) == 2
    assert len(store.read("raw.t")) == 6


# ------------------------------------------------- the reads, and the mechanism
def test_a_scalar_read_leaves_the_connection_able_to_register(store: Store):
    """The contract of `_scalar`/`_one`, asserted against the real connection.

    This is the mechanism rather than the symptom: on 1.x a one-row fetch also
    left a result open, it simply went unpunished. Asserting the connection is
    clean holds on every version.
    """
    store.write("raw.t", rows(4), mode="append", key=["id"])
    assert store._scalar(f'SELECT count(*) FROM {store._q("raw__t")}') == 4
    assert store._one(f'SELECT min(id), max(id) FROM {store._q("raw__t")}') == (0, 3)
    store.con.register("_probe", rows(1))
    store.con.unregister("_probe")          # raises if a result is still open


def test_reading_the_whole_surface_leaves_the_next_write_working(store: Store):
    """Every read an agent takes before an ingest, then the ingest.

    This is the user's session: `find_filings`, `list_companies`, `read_company`
    and then `ingest_filings`. Each read is a different code path to a scalar.
    """
    store.write("raw.t", rows(5), mode="append", key=["id"])
    store.table_info("raw.t")
    store.profile("raw.t")
    store.all_tables()
    store.exists("raw.t")
    store.max_time("raw.t")
    store.time_range("raw.t")
    store.read("raw.t", limit=1)
    store.query(f'SELECT count(*) FROM {store._q("raw__t")}')
    assert store.write("raw.t", rows(5, start=5), mode="append", key=["id"]) == 5
    assert len(store.read("raw.t")) == 10


def test_no_undrained_reads_remain():
    """The acceptance check, kept as a test so it cannot quietly come back."""
    offenders = {
        p.relative_to(SRC).as_posix(): [
            i for i, line in enumerate(p.read_text().splitlines(), 1)
            if ONE_ROW_FETCH in line
        ]
        for p in sorted(SRC.rglob("*.py"))
        if ONE_ROW_FETCH in p.read_text()
    }
    assert not offenders, (
        f"{ONE_ROW_FETCH} leaves a DuckDB result open, and the next register() on "
        f"that connection is a write that will fail. Use Store._scalar / _one: "
        f"{offenders}"
    )


# ------------------------------------------- the count the write path wasted
def test_the_write_path_does_not_count_the_whole_table(store: Store):
    """`_publish` returned a `count(*)` that `_write_parquet` deleted unread.

    That discarded query was what poisoned the connection in shape B, so the
    cheapest half of the fix was to stop asking it. An append must not read the
    rest of the table to report what it wrote.
    """
    store.write("raw.t", rows(50), mode="append", key=["id"])
    seen: list[str] = []
    real_scalar = store._scalar

    def spy(sql, params=None, con=None):
        seen.append(sql)
        return real_scalar(sql, params, con)

    store._scalar = spy
    try:
        assert store.write("raw.t", rows(2, start=50), mode="append", key=["id"]) == 2
    finally:
        del store._scalar
    counts = [q for q in seen if re.search(r"count\(\*\)\s+FROM\s+\"", q)]
    assert not counts, f"an append counted the whole table for nothing: {counts}"


def test_an_append_reports_what_it_wrote_not_the_table_total():
    """The reason `_publish`'s count was never the return value to begin with."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        s = Store(str(Path(d) / "corpus"))
        try:
            s.write("raw.t", rows(100), mode="append", key=["id"])
            assert s.write("raw.t", rows(16, start=100), mode="append", key=["id"]) == 16
            assert len(s.read("raw.t")) == 116
        finally:
            s.close()
