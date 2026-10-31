"""The parquet store, and the one thing it exists to do.

The rest of the suite runs against this backend too — `QANAT_TEST_STORE=parquet
uv run pytest` — so equivalence is covered there. What is here is the behaviour
that is new: a writer that does not block a reader, a file that never appears
half-written, and a metadata file that a reader never has to touch.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest

from qanat.store import Store, StoreBusy, is_parquet_dir


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(str(tmp_path / "corpus"))
    yield s
    s.close()


def rows(n: int, start: int = 0) -> pd.DataFrame:
    return pd.DataFrame({
        "sym": [f"S{(i + start) % 7}" for i in range(n)],
        "px": [float(i + start) for i in range(n)],
        "as_of": pd.to_datetime(["2024-01-01"] * n),
    })


# ------------------------------------------------------------------ what it is
def test_a_directory_is_a_parquet_store(tmp_path: Path):
    assert is_parquet_dir(str(tmp_path / "corpus"))
    assert is_parquet_dir(str(tmp_path))


def test_a_file_path_is_not(tmp_path: Path):
    """`./data/qanat.duckdb` has always meant one file and still does."""
    assert not is_parquet_dir(str(tmp_path / "q.duckdb"))


def test_one_directory_per_table(store: Store, tmp_path: Path):
    """A table is a directory of files, so an append can add one and leave the
    rest alone."""
    store.write("raw.prices", rows(3))
    store.write("normalized.prices", rows(2))
    tables = tmp_path / "corpus" / "tables"
    assert sorted(d.name for d in tables.iterdir()) == [
        "normalized__prices", "raw__prices"]
    assert [f.name for f in (tables / "raw__prices").glob("*.parquet")] == ["0000.parquet"]


# --------------------------------------------------- the reason this exists
def test_a_reader_is_not_blocked_by_a_writer(tmp_path: Path):
    """The whole point. A DuckDB file refuses a second process outright; this
    is what a team sharing a corpus runs into on the first day."""
    corpus = tmp_path / "corpus"
    Store(str(corpus)).write("raw.p", rows(10))

    writer = textwrap.dedent(f"""
        import sys, time, pandas as pd
        sys.path.insert(0, {str(Path(__file__).parents[1] / "src")!r})
        from qanat.store import Store
        s = Store({str(corpus)!r})
        for _ in range(3):
            n = 120_000
            s.write("raw.p", pd.DataFrame({{"sym": ["A"] * n, "px": [1.0] * n,
                                            "as_of": pd.to_datetime(["2024-01-01"] * n)}}))
            time.sleep(0.4)
        s.close()
    """)
    proc = subprocess.Popen([sys.executable, "-c", writer])
    try:
        import time
        time.sleep(0.5)                      # the writer is mid-flight
        reader = Store(str(corpus))          # a DuckDB file would raise StoreBusy here
        got = reader.query("select count(*) c from raw__p").c[0]
        assert got > 0
        reader.close()
    finally:
        proc.wait(timeout=30)


def test_a_reader_never_sees_a_half_written_file(store: Store, tmp_path: Path):
    """The write goes to a sibling `.tmp` and is renamed, which is atomic. So
    the real name points at a finished file or at nothing."""
    store.write("raw.p", rows(50_000))
    tables = tmp_path / "corpus" / "tables"
    assert not list(tables.glob("*.tmp")), "a temp file was left behind"
    # every file under the real name parses, which a partial one would not
    for f in tables.glob("*.parquet"):
        assert store.con.execute(
            f"select count(*) from read_parquet('{f}')").fetchone()[0] > 0


def test_two_stores_on_one_directory_share_the_instance(tmp_path: Path):
    """DuckDB attaches a file once per process whatever the mode, and opening
    a second Store beside the first is ordinary — building the graph does it."""
    a = Store(str(tmp_path / "corpus"))
    a.write("raw.p", rows(5))
    b = Store(str(tmp_path / "corpus"))
    assert b.read("raw.p").shape[0] == 5
    a.event("info", "t", "touching the metadata file from the first")
    b.event("info", "t", "and from the second")
    assert len(b.recent_events(10)) >= 2
    a.close()
    b.close()


# ------------------------------------------------------------- the metadata
def test_a_reader_does_not_open_the_metadata_file(store: Store, tmp_path: Path):
    """What keeps a teammate's query working while an ingest is running: the
    run log takes one writer, and reading data never asks for it."""
    store.write("raw.p", rows(4))
    store.read("raw.p")
    store.query("select * from raw__p")
    store.table_info("raw.p")
    store.all_tables()
    assert not store._meta_open
    assert not (tmp_path / "corpus" / "meta.duckdb").exists()


def test_asking_for_the_run_log_opens_it(store: Store, tmp_path: Path):
    store.start_run("j", "step", ["raw.p"])
    assert store._meta_open
    assert (tmp_path / "corpus" / "meta.duckdb").exists()


def test_table_info_has_no_timestamp_until_the_log_is_open(store: Store):
    """`read()` reaches `table_info`, so it must not drag the metadata file in.
    The cost is that a pure reader gets no last-written time."""
    store.write("raw.p", rows(3))
    assert store.table_info("raw.p").updated_at is None
    assert not store._meta_open


# ------------------------------------------------------------- writing rows
def test_replace_rewrites_the_whole_file(store: Store):
    store.write("raw.p", rows(10))
    store.write("raw.p", rows(4))
    assert len(store.read("raw.p")) == 4


def test_an_append_writes_one_new_file_and_leaves_the_rest(store: Store, tmp_path: Path):
    """The reason a table is a directory. Landing a day of filings should write
    a day of filings, not the whole corpus again."""
    store.write("raw.p", rows(1000))
    parts = tmp_path / "corpus" / "tables" / "raw__p"
    first = sorted(parts.glob("*.parquet"))
    before = first[0].stat().st_size, first[0].stat().st_mtime

    store.write("raw.p", rows(5, start=9000), mode="append")
    now = sorted(parts.glob("*.parquet"))
    assert len(now) == 2, "the append should have added a file"
    assert (now[0].stat().st_size, now[0].stat().st_mtime) == before, \
        "the existing file was touched"
    # the new file holds the new rows and only those
    in_new = store.con.execute(
        f"select count(*) from read_parquet('{now[1]}')").fetchone()[0]
    assert in_new == 5
    assert len(store.read("raw.p")) == 1005


def test_a_key_that_cannot_match_takes_the_cheap_path(store: Store, tmp_path: Path):
    """Asked from the parquet footers, which record the range each file holds,
    so a day of prices after the last day stored needs no rewrite."""
    store.write("raw.p", pd.DataFrame({
        "sym": ["A", "B"], "date": pd.to_datetime(["2024-01-01"] * 2), "px": [1.0, 2.0]}))
    parts = tmp_path / "corpus" / "tables" / "raw__p"
    untouched = min(parts.glob("*.parquet")).stat().st_mtime

    store.write("raw.p", pd.DataFrame({
        "sym": ["A"], "date": pd.to_datetime(["2030-01-01"]), "px": [3.0]}),
        mode="append", key=["sym", "date"])
    assert min(parts.glob("*.parquet")).stat().st_mtime == untouched
    assert len(store.read("raw.p")) == 3


def test_a_key_that_could_match_rewrites(store: Store, tmp_path: Path):
    """And when it really could already be there, correctness wins: the table
    is rewritten so the old row is replaced rather than duplicated."""
    store.write("raw.p", pd.DataFrame({
        "sym": ["A", "B"], "date": pd.to_datetime(["2024-01-01"] * 2), "px": [1.0, 2.0]}))
    store.write("raw.p", pd.DataFrame({
        "sym": ["A"], "date": pd.to_datetime(["2024-01-01"]), "px": [99.0]}),
        mode="append", key=["sym", "date"])
    got = store.read("raw.p").set_index("sym")["px"].to_dict()
    assert got == {"A": 99.0, "B": 2.0}
    parts = tmp_path / "corpus" / "tables" / "raw__p"
    assert len(list(parts.glob("*.parquet"))) == 1, "a rewrite leaves one file"


def test_a_table_written_as_one_file_still_opens(tmp_path: Path):
    """0.6 wrote `facts.parquet`; 0.7 writes `facts/0000.parquet`. An existing
    project should not have to be told to run anything."""
    import duckdb

    tables = tmp_path / "corpus" / "tables"
    tables.mkdir(parents=True)
    duckdb.connect().execute(
        f"copy (select 1 a, 'x' b) to '{tables / 'raw__old.parquet'}' (format parquet)")
    s = Store(str(tmp_path / "corpus"))
    try:
        assert s.all_tables() == ["raw.old"]
        assert len(s.read("raw.old")) == 1
        assert (tables / "raw__old" / "0000.parquet").exists()
    finally:
        s.close()


def test_append_adds_to_what_is_there(store: Store):
    store.write("raw.p", rows(3))
    store.write("raw.p", rows(2, start=100), mode="append")
    assert len(store.read("raw.p")) == 5


def test_a_key_turns_an_append_into_an_upsert(store: Store):
    store.write("raw.p", pd.DataFrame({"sym": ["A", "B"], "px": [1.0, 2.0]}))
    store.write("raw.p", pd.DataFrame({"sym": ["B", "C"], "px": [9.0, 3.0]}),
                mode="append", key=["sym"])
    got = store.read("raw.p").set_index("sym")["px"].to_dict()
    assert got == {"A": 1.0, "B": 9.0, "C": 3.0}


def test_a_key_holding_a_null_still_matches_itself(store: Store):
    """A null never equals a null in SQL, so a blank key appended the row again
    on every poll, for ever."""
    one = pd.DataFrame({"sym": [None], "px": [1.0]})
    store.write("raw.p", one)
    store.write("raw.p", pd.DataFrame({"sym": [None], "px": [2.0]}),
                mode="append", key=["sym"])
    assert len(store.read("raw.p")) == 1


def test_an_empty_frame_clears_the_table_and_keeps_its_columns(store: Store):
    """A step with nothing to say is an answer. Leaving the last pass's rows
    would read downstream as a fresh one."""
    store.write("raw.p", rows(5))
    store.write("raw.p", pd.DataFrame(columns=["sym", "px", "as_of"]))
    after = store.read("raw.p")
    assert len(after) == 0
    assert list(after.columns) == ["sym", "px", "as_of"]


def test_columns_that_do_not_match_are_refused_on_append(store: Store):
    store.write("raw.p", rows(2))
    with pytest.raises(ValueError, match="do not match"):
        store.write("raw.p", pd.DataFrame({"sym": ["A"], "other": [1]}), mode="append")


def test_an_append_lines_columns_up_by_name(store: Store):
    """A feed that reordered its columns wrote dates into the symbol column."""
    store.write("raw.p", pd.DataFrame({"a": [1], "b": ["x"]}))
    store.write("raw.p", pd.DataFrame({"b": ["y"], "a": [2]}), mode="append")
    got = store.read("raw.p").sort_values("a")
    assert list(got["a"]) == [1, 2] and list(got["b"]) == ["x", "y"]


# -------------------------------------------------------------- the rest of it
def test_drop_removes_the_file(store: Store, tmp_path: Path):
    store.write("raw.p", rows(3))
    assert store.drop("raw.p") == 3
    assert not (tmp_path / "corpus" / "tables" / "raw__p.parquet").exists()
    assert not store.exists("raw.p")


def test_retention_rewrites_rather_than_deletes(store: Store):
    """A parquet file is not edited, so dropping old rows is a rewrite of the
    rows that stay."""
    old = pd.DataFrame({"sym": ["A"], "as_of": pd.to_datetime(["2000-01-01"])})
    new = pd.DataFrame({"sym": ["B"], "as_of": pd.to_datetime(["2099-01-01"])})
    store.write("raw.p", pd.concat([old, new], ignore_index=True))
    assert store.prune_rows("raw.p", "as_of", "2020-01-01") == 1
    assert list(store.read("raw.p")["sym"]) == ["B"]


def test_a_store_reopened_finds_its_tables(tmp_path: Path):
    a = Store(str(tmp_path / "corpus"))
    a.write("raw.p", rows(6))
    a.close()
    b = Store(str(tmp_path / "corpus"))
    assert b.all_tables() == ["raw.p"] and len(b.read("raw.p")) == 6
    b.close()


def test_the_as_of_views_clip_a_read(store: Store):
    store.write("raw.p", pd.DataFrame({
        "sym": ["A", "B"], "as_of": pd.to_datetime(["2024-01-01", "2025-01-01"])}))
    store.open_pit("2024-06-01", {})
    try:
        assert list(store.read("raw.p")["sym"]) == ["A"]
    finally:
        store.close_pit()
    assert len(store.read("raw.p")) == 2


def test_opening_the_run_log_does_not_lift_the_as_of_views(store: Store):
    """The bug this is here for: attaching the metadata file reset the search
    path, which took the as-of views off the front. Every step then read the
    live table and the lookahead guard caught it as a leak."""
    store.write("raw.p", pd.DataFrame({
        "sym": ["A", "B"], "as_of": pd.to_datetime(["2024-01-01", "2025-01-01"])}))
    store.open_pit("2024-06-01", {})
    try:
        store.start_run("j", "step", ["raw.p"])     # attaches the metadata file
        assert list(store.read("raw.p")["sym"]) == ["A"], "the as-of views were lost"
        assert store.con.execute("select count(*) from raw__p").fetchone()[0] == 1
    finally:
        store.close_pit()


def test_the_same_moment_refuses_a_duckdb_file(tmp_path: Path):
    """The contrast, run the same way as the parquet case above.

    A second *process* is what a DuckDB file refuses; inside one process the
    connections share. So this holds the file from a subprocess, which is what
    a teammate's ingest actually is."""
    db = tmp_path / "one.duckdb"
    holder = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {str(Path(__file__).parents[1] / "src")!r})
        from qanat.store import Store
        s = Store({str(db)!r})
        print("held", flush=True)
        time.sleep(3)
        s.close()
    """)
    proc = subprocess.Popen([sys.executable, "-c", holder], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == "held"
        with pytest.raises(StoreBusy, match="already open"):
            Store(str(db))
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_appending_to_a_table_that_is_not_there_yet_creates_it(store: Store):
    """The branch that creates the table also makes `exists` true, so a second
    check of it ran the append path with no columns worked out and the first
    cold build of the example failed."""
    store.write("raw.fresh", rows(3), mode="append", key=["sym"])
    assert len(store.read("raw.fresh")) == 3
    store.write("raw.fresh", rows(2, start=500), mode="append", key=["sym"])
    assert len(store.read("raw.fresh")) == 5


# --------------------------------------------------- the log outlives the process
@pytest.mark.parametrize("url", ["dir", "file"])
def test_the_run_log_survives_a_close_on_either_store(tmp_path: Path, url: str):
    """One shape everywhere, and this is where it was not.

    A parquet store's own connection is `:memory:`, and its search path is
    `memory.main` then `meta.main` -- data first, because a bare name means a
    data table. So `CREATE TABLE IF NOT EXISTS _qanat_runs` resolved to the
    front of that path and built the run log, the event log and the applied
    state **in memory**, in a file-backed store.

    What it cost, on the parquet store only: `list_runs` came back empty after
    a restart, a subscription's last run read as never, and `plan` found no
    applied state -- so every job looked like it had never run and every pass
    reported the whole project as new. The duckdb-file store was always right,
    which is why nothing caught it: the suite writes and reads in one process.
    """
    target = str(tmp_path / "data") if url == "dir" else str(tmp_path / "q.duckdb")

    s = Store(target)
    run = s.start_run("land", "source", ["raw.a"])
    s.end_run(run, "ok", 42)
    s.set_state("land", "digest-1", '{"spec": 1}')
    s.event("ok", "land", "landed 42 rows")
    s.close()

    again = Store(target)
    try:
        runs = again.recent_runs(5)
        assert [r["job_id"] for r in runs] == ["land"]
        assert runs[0]["status"] == "ok"
        assert runs[0]["rows_out"] == 42
        assert again.load_state() == {"land": {"digest": "digest-1",
                                               "spec": '{"spec": 1}'}}
        assert [e["message"] for e in again.recent_events(5)] == ["landed 42 rows"]
        # And the reader connection sees it too, which is the one a tool uses.
        assert again.last_run_by_job()["land"]["rows_out"] == 42
    finally:
        again.close()


def test_the_metadata_tables_are_not_built_in_memory(tmp_path: Path):
    """The shape of the bug, held directly: nothing named `_qanat_*` may live
    in the in-memory database of a file-backed store."""
    s = Store(str(tmp_path / "data"))
    try:
        s.start_run("land", "source", ["raw.a"])      # forces the catalogue open
        where = s.con.execute(
            "SELECT DISTINCT database_name FROM duckdb_tables() "
            "WHERE table_name LIKE '_qanat%'").fetchall()
        assert where == [("meta",)], f"metadata lives in {where}"
    finally:
        s.close()
