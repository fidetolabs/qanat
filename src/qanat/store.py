"""Where the tables live: the run history, the event log, and the data itself.

DuckDB is the engine -- it runs a `.sql` transform -- and the storage is
one of two kinds, with the SQL a transform writes identical across both:

| `store:` | kind | who can open it at once |
| --- | --- | --- |
| a directory | **parquet** | one writer **and** any number of readers |
| a `.duckdb` path | duckdb | one process, full stop |

**Parquet is the one to reach for, and the reason is the third column.** A
DuckDB file takes an exclusive lock, so a second process is refused outright --
not slowed, refused. That was read for a long time as a hosting limit. It is
not: it is a limit on the second person, and a team sharing a corpus meets it on
the first day. With parquet the writer adds a file nobody is reading, so a
reader is never blocked and never waits.

**There was a third kind, a Postgres server reached by attaching it, and it is
gone.** The corpus is one writer, append-only, read in wide scans, which is
what parquet is for -- so the server bought nothing the files do not, and cost a
second code path through every write, every as-of view and every migration.

A table lives under `<stage>__<name>`, so the stage a table belongs to is
readable from its name alone -- over MCP, in the DuckDB CLI, and in anything else
that opens the database.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

#: Where a parquet store keeps its two halves. Data is one file per table and is
#: rewritten whole; metadata is row-at-a-time and stays in DuckDB, opened only
#: when something actually asks for it.
TABLES_DIR = "tables"
META_FILE = "meta.duckdb"

#: One DuckDB instance per parquet directory, per process, shared by every
#: `Store` opened on it.
#:
#: Not an optimisation. DuckDB allows a file to be attached once per process,
#: whatever the mode, so two `Store` objects on one directory each attaching
#: `meta.duckdb` is a hard error -- and two is ordinary: building the graph
#: opens one beside the session's. Each `Store` takes a cursor instead. Cursors
#: share the catalogue, which is right because they are reading the same files,
#: and keep their own `search_path`, which is what lets one hold a screen open
#: while another does not.
_SHARED: dict[str, list] = {}
_SHARED_LOCK = threading.Lock()


def _shared_duckdb(key: str):
    """The process's connection for one parquet directory, and a cursor on it."""
    with _SHARED_LOCK:
        entry = _SHARED.get(key)
        if entry is None:
            entry = [duckdb.connect(), 0]
            _SHARED[key] = entry
        entry[1] += 1
        return entry[0].cursor()


def _release_duckdb(key: str) -> None:
    """Let go of one cursor's share. The last one out closes the instance."""
    with _SHARED_LOCK:
        entry = _SHARED.get(key)
        if entry is None:
            return
        entry[1] -= 1
        if entry[1] <= 0:
            with contextlib.suppress(Exception):
                entry[0].close()
            _SHARED.pop(key, None)


class StoreBusy(RuntimeError):
    """The database is open somewhere else. Says which door to use instead."""

#: The job layer: one definition, for one exact datetime. Two of the four
#: objects already existed under other names -- `_qanat_runs` was the execution
#: layer and `_qanat_state` was definition versioning -- and this is the one
#: that did not. **It exists because of retries rather than backfills.** A poll
#: attempted three times is one job and three executions; with one row per
#: attempt and nothing grouping them, *"did the 07:00 poll succeed?"* had no
#: object to ask.
JOBS = "_qanat_job"
#: One attempt. Was `_qanat_runs`, which is the same thing under a word that
#: did not say what it was an execution *of*.
EXECUTIONS = "_qanat_job_execution"
EVENTS = "_qanat_events"
#: What a definition looked like the last time it worked. Was `_qanat_state`,
#: which named the file rather than the thing in it.
VERSIONS = "_qanat_job_definition_version"
#: Which columns identify one row in a table. Told to the store by whoever
#: writes it, and remembered, because the reader that has to pick the current
#: row among two versions of one key is not the writer and has no way to ask.
#:
#: **It used to live here and that was a correctness hole.** `_load_keys`
#: returns an empty map when the metadata file is not open -- deliberately,
#: because opening it would make every reader take the writer lock -- and
#: `_current` then found no key and skipped the pick entirely. So a program that
#: never opened `meta.duckdb` got **every version of a corrected row** instead of
#: the newest one, and the `now` view was still built, so nothing reported the
#: absence.
#:
#: The registry is a tiny map of table name to key columns. It is in a plain file
#: beside the store now, read with no lock by anybody. Kept as a name only so a
#: store written before the move can be migrated out of it.
KEYS = "_qanat_table_key"

#: The tables that stay in the metadata file: row-at-a-time, appended, one owner.
META = (JOBS, EXECUTIONS, EVENTS, VERSIONS)

#: Where the key registry lives: beside the store, lock-free. A parquet store is
#: a directory so it goes in it; a DuckDB store is one file so the sidecar is
#: named after it, because two projects may share a directory.
KEYS_FILE = ".qanat-keys.json"

#: What these tables were called before the vocabulary changed. Renamed in place
#: on open, so a store written by an older version keeps its history instead of
#: starting again with an empty log.
#: Schema holding the as-of views a screen reads through. Named, not `asof`,
#: because DuckDB reserves that word for ASOF JOIN.
PIT = "qanat_pit"

#: Schema holding one view per table that keeps the engine's own columns out of
#: a `SELECT *`. **Not tidiness.** A transform reading `SELECT * FROM
#: raw__documents` would carry the upstream's execution id into its own output,
#: and the engine would then stamp a second one -- two columns with one name.
#: The physical table keeps the column; `lineage` is what reads it.
NOW = "qanat_now"

#: The execution that wrote a row. One column, set by the engine at write time.
#:
#: **It is what the one-writer-per-table rule was standing in for.** That rule's
#: stated reason was that otherwise nothing can say which pass made the rows in
#: a table -- and measured, that was already the situation: a `raw.documents` row
#: carried `source: "sec-edgar"` and a JSON blob of the SEC's keys, and no run id
#: anywhere. Attribution was job-to-table, never row-to-execution, and
#: `ingest_filings` wrote that same table without opening a job at all.
#:
#: It resolves up the chain -- execution to job to definition to that
#: definition's version -- so the row carries one narrow value rather than a copy
#: of the whole provenance.
EXEC_COL = "_qanat_execution"

#: When this row was written, as a monotonic counter. **A different question from
#: which execution wrote it, and the reader needs this one.** Picking the current
#: row among two versions of a key means ordering by write, and an execution id
#: answers that only for rows a job wrote -- a row written outside one carries no
#: execution, and two of those would order arbitrarily. One row, possibly the
#: wrong one, is worse than a failure.
SEQ_COL = "_qanat_written"

#: Columns the engine owns. A frame arriving with one of these in it is an error:
#: a transform cannot set, forge or omit its own provenance.
ENGINE_COLS = (EXEC_COL, SEQ_COL)

#: Who is causing work right now, recorded on every event.
#:
#: The event log carries it, because a project that changes while you are reading
#: a result should say who changed it. An agent driving over MCP is the case that
#: matters: `qanat mcp` sets this once for its whole process, the scheduler sets
#: it per job, and everything else is a person at the CLI.
#:
#: A ContextVar rather than a global: the scheduler runs jobs in worker threads,
#: and each thread gets its own value instead of racing over one.
ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("qanat_actor", default="you")


#: Which execution is writing right now. A ContextVar for the same reason
#: `ACTOR` is one: the scheduler runs jobs in worker threads, and each thread
#: needs its own value instead of racing over one.
EXECUTION: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "qanat_execution", default=None)


def current_actor() -> str:
    return ACTOR.get()


def current_execution() -> int | None:
    """The execution every write from here on is attributed to.

    None outside a job. A row written then reads as unknown provenance rather
    than being guessed at -- which is what rows written before this existed read
    as, and they stay that way.
    """
    return EXECUTION.get()


@contextlib.contextmanager
def writing_as(execution_id: int | None):
    """Attribute every write inside this block to one execution."""
    token = EXECUTION.set(execution_id)
    try:
        yield
    finally:
        EXECUTION.reset(token)


def set_actor(who: str) -> None:
    """Name what is running things from here on. `you`, `agent`, or `schedule`."""
    ACTOR.set(who)


@contextlib.contextmanager
def acting_as(who: str) -> Any:
    """Name the cause for the duration of one block, then put it back."""
    token = ACTOR.set(who)
    try:
        yield
    finally:
        ACTOR.reset(token)

#: Column names a table's timestamp may go by, most specific first.
TIME_COLS = ("as_of", "ts", "timestamp", "datetime", "date", "time", "fetched_at",
             "created_at", "updated_at", "year")


def _ident(name: str) -> str:
    """A quoted identifier, with any embedded quote doubled.

    Names are validated in `models.py`, so nothing legal reaches here with a quote
    in it. This is the second wall: an identifier is interpolated into DDL, and a
    wall that costs one line should not be left out.
    """
    return str(name).replace('"', '""')


#: DuckDB's names for the integer types, as `duckdb_columns()` reports them.
INT_TYPES = ("TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
             "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT")


def as_instant(col: str, dtype: str | None = None) -> str:
    """SQL that turns a time column into a comparable UTC instant.

    Two things used to go wrong here, and the type is decided in Python rather
    than in SQL because a `CASE` over a column's type cannot be written portably.

    **Offsets were thrown away.** A bare `CAST(x AS TIMESTAMP)` keeps the wall clock,
    so `16:00-05:00` and `16:00Z` compared equal and a New York close was visible
    four hours before it happened. The cast now goes through TIMESTAMPTZ.

    **Integers are a clock too, and not all the same one.** DuckDB refuses to cast
    them at all, so rather than fail three layers down they are read by magnitude:

        1000 .. 9999      a year        -- annual macro data says `2024`
        1e8  .. 1e11      epoch seconds -- 1973 to 5138
        above 1e11        epoch millis  -- what USGS, Slack and Binance send

    Anything smaller is not a date anybody meant, and is left null rather than
    silently read as a few minutes after 1970.
    """
    q = f'"{_ident(col)}"'
    if dtype and str(dtype).upper().split("(")[0] in INT_TYPES:
        n = f"CAST({q} AS BIGINT)"
        return (
            f"CASE "
            f"WHEN {n} BETWEEN 1000 AND 9999 THEN make_timestamp({n}, 1, 1, 0, 0, 0) "
            f"WHEN abs({n}) > 100000000000 THEN CAST(to_timestamp({n} / 1000.0) AS TIMESTAMP) "
            f"WHEN abs({n}) > 100000000 THEN CAST(to_timestamp({n}) AS TIMESTAMP) "
            f"ELSE NULL END"
        )
    return f"CAST(TRY_CAST({q} AS TIMESTAMPTZ) AT TIME ZONE 'UTC' AS TIMESTAMP)"


#: A physical `stage__table` name inside a query. Both halves match the name rule
#: `models.py` enforces, so this cannot collide with an ordinary identifier.
_TABLE_TOKEN = re.compile(r"\b([a-z][a-z0-9_]*)__([a-z][a-z0-9_]*)\b")
_SQL_NOISE = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'", re.DOTALL)


def tables_named(sql: str) -> set[str]:
    """Every `stage.table` a query mentions, ignoring comments and string literals.

    `ctx.read` refuses a table a transform did not declare, and says why. A `.sql` body
    could reach the same table anyway, so the edge the graph drew was not where the
    data came from, `stale()` never marked the consumer, and `check` warned that a
    table read by two transforms "is never read by anything".
    """
    return {f"{a}.{b}" for a, b in _TABLE_TOKEN.findall(_SQL_NOISE.sub(" ", sql or ""))}


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Land a column that is entirely null as text, not as a guess.

    A field that is optional in a feed -- a US holiday's `counties`, a symbol on a
    payload row -- is null in the first response that happens not to carry it, and
    pandas types that as INTEGER. The type sticks, and the day the field arrives with
    real data every poll fails on a cast that names neither the feed nor the field.
    Text is the one type that does not close the door; a transform can cast it later.
    """
    empty = [c for c in df.columns if df[c].isna().all()]
    return df.astype({c: "string" for c in empty}) if empty else df


def phys(ref: str) -> str:
    """'features.mom_20' -> 'features__mom_20'"""
    stage, _, table = ref.partition(".")
    return f"{stage}__{table}" if table else stage


def unphys(name: str) -> str:
    stage, _, table = name.partition("__")
    return f"{stage}.{table}" if table else name


@dataclass
class TableInfo:
    ref: str
    stage: str
    name: str
    rows: int
    columns: list[tuple[str, str]]
    updated_at: str | None


def is_parquet_dir(target: str) -> bool:
    """Is this `store:` a parquet directory rather than a single file?

    A directory, or a path with no file extension. `./data/qanat.duckdb` is a
    file and stays one; `./data` is a parquet store. Anything that already
    exists settles it by what it is.
    """
    path = Path(target)
    if path.is_dir():
        return True
    if path.exists():
        return False
    return path.suffix == ""


class Store:
    """One database: a directory of parquet files, or a single DuckDB file."""

    def __init__(self, target: str | Path):
        self.target = str(target)
        self._lock = threading.RLock()

        if is_parquet_dir(self.target):
            self.kind = "parquet"
            self.path = Path(self.target)
            self.dir = self.path
            (self.dir / TABLES_DIR).mkdir(parents=True, exist_ok=True)
            self.label = str(self.dir)
            # In memory, and that is the point: nothing on disk is held open, so
            # another process may read the same parquet files at the same time.
            self._shared_key = str(self.dir.resolve())
            self.con = _shared_duckdb(self._shared_key)
            self._meta_open = False
        else:
            self.kind = "duckdb"
            self.path = Path(self.target)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.label = str(self.path)
            try:
                self.con = duckdb.connect(str(self.path))
            except duckdb.IOException as exc:
                if "lock" not in str(exc).lower():
                    raise
                raise StoreBusy(
                    f"{self.path} is already open in another process.\n"
                    "  A DuckDB file takes one writer at a time, so the CLI, the scheduler and\n"
                    "  an agent cannot each open it. Pick one:\n"
                    "    · stop whatever is serving this project, then run this again, or\n"
                    "    · reach it through the process that already has it: `qanat serve --mcp`\n"
                    "      serves MCP from one that holds the store, or\n"
                    "    · move the project to a parquet store (`store: ./data`), which takes any\n"
                    "      number of readers beside the writer"
                ) from exc
        # Without this the meaning of a timestamp depends on the machine: a stamp
        # written as UTC renders in local time, and an as-of comparison is then off
        # by the offset. It passed CI on a UTC box and was nine hours wrong in Seoul.
        self.con.execute("SET TimeZone='UTC'")
        self.home = self._scalar("SELECT current_schema()")
        self.as_of: str | None = None
        self._pit_overrides: dict[str, str] = {}
        #: Physical names that have a `now` view over them. Held rather than
        #: asked for, so a read never pays a catalogue query to find out.
        self._now: set[str] = set()
        #: physical name -> the columns that identify one row there
        self._keys: dict[str, list[str]] = {}
        self._readers = threading.local()
        if self.kind == "parquet":
            # Needs `home`, so it waits until the connection is settled.
            self._publish_views()
        else:
            self._ensure_meta()
            self._recover()
        self._load_keys()
        self._build_now_views()
        self.con.execute(f"SET search_path='{self._search_path()}'")

    def __str__(self) -> str:
        return f"{self.kind}: {self.label}"

    # ---- parquet: files on disk, views over them -----------------------------
    def _pq(self, name: str) -> Path:
        """A table is a directory of parquet files, not one file.

        One file meant every write rewrote the whole table: adding one day of
        filings to a 100-million-row table wrote 892 MB to land 2 MB. A
        directory lets an append add a file and leave the rest alone, which is
        the thing parquet was chosen for.
        """
        return self.dir / TABLES_DIR / name

    def _parts(self, name: str) -> list[Path]:
        d = self._pq(name)
        return sorted(d.glob("*.parquet")) if d.is_dir() else []

    def _publish_views(self) -> None:
        """Point a view at every table directory.

        After this the rest of the class does not know it is reading files:
        `_q()` returns the same quoted name it always did, and a view answers it.
        """
        root = self.dir / TABLES_DIR
        for d in sorted(root.iterdir() if root.is_dir() else []):
            if d.is_dir():
                self._view(d.name)

    def _view(self, name: str) -> None:
        parts = self._parts(name)
        if not parts:
            self.con.execute(f'DROP VIEW IF EXISTS {self._q(name)}')
            return
        glob = str(self._pq(name) / "*.parquet").replace(chr(39), chr(39) * 2)
        # union_by_name, because a later file may order its columns differently
        # and lining them up by position would write dates into a symbol column.
        self.con.execute(
            f"CREATE OR REPLACE VIEW {self._q(name)} AS "
            f"SELECT * FROM read_parquet('{glob}', union_by_name=true)"
        )

    def _write_part(self, name: str, select_sql: str, stem: str) -> Path:
        """Write one parquet file into a table's directory, atomically.

        The write goes to a sibling `.tmp` and is then renamed. A rename within
        one directory is atomic, so a reader globbing for `*.parquet` sees a
        finished file or no file, never a half-written one. A reader already
        part-way through a scan keeps the file it opened, because the old inode
        survives until it lets go.

        This is the whole of what parquet costs over a database, and it is two
        lines. What it buys is that the reader was never asked to wait.
        """
        d = self._pq(name)
        d.mkdir(parents=True, exist_ok=True)
        final = d / f"{stem}.parquet"
        tmp = d / f"{stem}.parquet.tmp"
        self.con.execute(
            f"COPY ({select_sql}) TO '{str(tmp).replace(chr(39), chr(39) * 2)}' (FORMAT parquet)")
        tmp.replace(final)
        return final

    def _next_stem(self, name: str) -> str:
        parts = self._parts(name)
        nums = [int(p.stem) for p in parts if p.stem.isdigit()]
        return f"{(max(nums) + 1 if nums else 0):04d}"

    # ---- reads that do not poison the connection -----------------------------
    #
    # A one-row fetch hands back its row without reaching end-of-stream, so the
    # connection keeps an *unfinished result*. DuckDB 1.x ignored that. DuckDB
    # 2.0 refuses `register()` and `unregister()` on such a connection while
    # still tolerating further `execute()` -- so a scalar read taken anywhere
    # breaks the next write, and nothing between them complains. Every write
    # registers a frame, so that asymmetry made the product read-only.
    #
    # These two drain. Nothing below reads a scalar any other way, and that is
    # the whole reason `register` is safe where it is used. The prose here says
    # "one-row fetch" rather than naming the method, so that grepping this file
    # for the method name stays a guard that finds a regression and nothing else
    # -- `test_no_undrained_reads_remain` greps exactly that.

    def _one(self, sql: str, params: Sequence[Any] | None = None,
             con: Any = None) -> tuple[Any, ...] | None:
        """The first row, with the result drained, or None if there is no row."""
        rows = (con if con is not None else self.con).execute(
            sql, list(params) if params else []).fetchall()
        return tuple(rows[0]) if rows else None

    def _scalar(self, sql: str, params: Sequence[Any] | None = None,
                con: Any = None) -> Any:
        """The first column of the first row, drained. See `_one`."""
        row = self._one(sql, params, con)
        return row[0] if row is not None else None

    def _release(self, frame: str = "_incoming") -> None:
        """Unregister the incoming frame, without letting cleanup fail the write.

        This runs in a `finally` reached *after* the rows are on disk. An
        exception from here turns a write that succeeded into a write the caller
        was told had failed -- which is precisely how the DuckDB 2.0 break
        presented: the parquet file landed, the view was rebuilt, and then
        `unregister` threw from the cleanup. A frame left registered is a name
        waiting to be reused; a landed write reported as an error is a wrong
        answer, and the worse of the two by far.
        """
        try:
            self.con.unregister(frame)
        except Exception as exc:  # noqa: BLE001 -- cleanup cannot fail a landed write
            with contextlib.suppress(Exception):  # nor can logging the cleanup
                self.event("warn", "store", f"could not release {frame}: {exc}")

    def _rows(self, name: str) -> int:
        return int(self._scalar(f"SELECT count(*) FROM {self._q(name)}"))

    def _publish(self, name: str, select_sql: str) -> None:
        """Replace a table: one new file, and the old ones go.

        The new file is written first and the old ones are removed after, so a
        reader is never looking at an empty directory.

        Returns nothing. This used to return `_rows(name)`, and `_write_parquet`
        -- its main caller -- assigned that and then deleted it unread. Every
        write paid for a `count(*)` over the whole table to throw the answer
        away, and under DuckDB 2.0 that discarded query was the thing that broke
        the cleanup *after* the rows had landed. A caller that wants the total
        asks `_rows` for it, outside the register window.
        """
        before = self._parts(name)
        self._write_part(name, select_sql, "0000" if not before else self._next_stem(name))
        for old_part in before:
            old_part.unlink(missing_ok=True)
        self._view(name)

    def _add_part(self, name: str, select_sql: str) -> None:
        """Append: one new file, and nothing existing is touched.

        This is the cheap path and the reason a table is a directory. Landing
        a day of filings writes a day of filings, not the whole corpus again.
        """
        self._write_part(name, select_sql, self._next_stem(name))
        self._view(name)

    def _overlaps(self, name: str, key: Sequence[str], frame: str) -> bool:
        """Could any incoming key already be in a file that is here?

        Every parquet file records the smallest and largest value it holds for
        each column, so this is answered from the footers without reading a
        row. When the answer is no -- which is every ordinary time-series
        append, because the new dates are after all the old ones -- the write
        is a new file and nothing is rewritten.
        """
        parts = self._parts(name)
        if not parts:
            return False
        bounds = {k: self._one(
            f'SELECT min("{_ident(k)}"), max("{_ident(k)}") FROM {frame}')
            for k in key}
        if any(lo is None for lo, _ in bounds.values()):
            return True          # a null key could match anything
        for part in parts:
            quoted = str(part).replace(chr(39), chr(39) * 2)
            stats = {
                row[0]: (row[1], row[2])
                for row in self.con.execute(
                    f"SELECT path_in_schema, stats_min_value, stats_max_value "
                    f"FROM parquet_metadata('{quoted}')").fetchall()
            }
            # The file overlaps when every key column's range overlaps. A single
            # column that cannot match rules the whole file out.
            if all(
                k not in stats or stats[k][0] is None
                or not (str(stats[k][1]) < str(bounds[k][0])
                        or str(stats[k][0]) > str(bounds[k][1]))
                for k in key
            ):
                return True
        return False

    def _search_path(self) -> str:
        """Where a bare name is looked up, in order.

        One place builds this string, because three things change it and they
        used to each set it themselves. The as-of views go first when a screen
        is open -- that is what makes a transform read the past without knowing it.
        Then the data. Then the metadata catalogue, when a parquet store has
        opened one.

        The bug this exists to stop: attaching the metadata file reset the
        search path, which silently took the as-of views off the front. Every
        transform then read the live table and the lookahead guard caught it as a
        leak, which is the right failure for the wrong reason.
        """
        parts = []
        if self.as_of is not None:
            parts.append(PIT)
        # Then the `now` views, so a bare `stage__table` in a transform's SQL
        # resolves to the one that hides the engine's columns. Writes are pinned
        # to the home schema by `_q`, so this cannot move one.
        if self._now:
            parts.append(NOW)
        if self.kind == "parquet":
            parts.append("memory.main")
            if self._meta_open:
                parts.append("meta.main")
        else:
            parts.append(self.home)
        return ",".join(parts)

    def _meta(self) -> None:
        """Attach the metadata file, the first time something actually needs it.

        Runs, events and applied state are row-at-a-time and belong in a
        database. That database is a DuckDB file, which takes one writer -- so
        it is opened late and only by a caller that asked for it. A reader that
        only queries data never touches it, which is what lets a teammate read
        the corpus while an ingest is running.
        """
        if self.kind != "parquet" or self._meta_open:
            return
        path = self.dir / META_FILE
        already = self._scalar(
            "SELECT count(*) FROM duckdb_databases() WHERE database_name = 'meta'")
        if already:
            # Another Store on this directory got here first. Same instance, so
            # the attachment is already ours to use.
            self._meta_open = True
            self.con.execute(f"SET search_path='{self._search_path()}'")
            return
        try:
            self.con.execute(f"ATTACH '{str(path).replace(chr(39), chr(39) * 2)}' AS meta")
        except duckdb.Error as exc:
            if "lock" not in str(exc).lower():
                raise
            raise StoreBusy(
                f"{path} is held by another process.\n"
                "  The run log and the event log take one writer at a time. The data itself\n"
                "  does not -- reading the tables works while somebody else is ingesting.\n"
                "  What needs the log: running a job, and asking what has run."
            ) from exc
        # Bare `_qanat_runs` has to find the metadata catalogue, and a bare table
        # name has to keep finding the views. Search path, in that order.
        self._meta_open = True
        self.con.execute(f"SET search_path='{self._search_path()}'")
        self._ensure_meta()
        self._recover()
        # The keys live in the file that has just been attached, so this is the
        # first moment they can be read. A read taken before now saw every row
        # of a corrected key instead of the current one -- which is why the
        # views are rebuilt here rather than left as they were.
        self._load_keys()
        self._build_now_views()
        self.con.execute(f"SET search_path='{self._search_path()}'")

    #: What a program that holds this store writes about itself, so that a
    #: program refused the lock can name the holder.
    #:
    #: **`StoreBusy` only knows that a file was locked.** The operating system's
    #: lock carries no pid, no port and no idea which doors the holder serves, so
    #: the refusal could offer general advice and nothing specific. This file is
    #: how it gets specific.
    #:
    #: **It is advisory and the DuckDB lock stays the authority.** A `kill -9`
    #: leaves it behind, and a stale one is harmless precisely because nothing
    #: trusts it: if the lock can be taken, the holder is gone, and the file is
    #: overwritten without a prompt and without a warning. Failing to write it is
    #: never a refusal either -- a read-only store directory still serves.
    LOCK_FILE = ".qanat-lock"

    @property
    def _lock_note(self) -> Path:
        """Beside the store, not inside it.

        A parquet store is a directory, so the note goes in it, next to
        `meta.duckdb` and `tables/`. A DuckDB store is one file, so the note is
        named after it -- `corpus.duckdb.qanat-lock` -- because two projects may
        sit in one directory and one shared note could not say which was held.
        """
        if self.kind == "parquet":
            return self.dir / self.LOCK_FILE
        return self.path.parent / f"{self.path.name}{self.LOCK_FILE}"

    def _read_lock_note(self) -> dict[str, Any]:
        """What the holder said about itself, or an empty dict."""
        import json as _json

        try:
            got = _json.loads(self._lock_note.read_text())
        except Exception:  # noqa: BLE001 -- absent, truncated, or not ours
            return {}
        return got if isinstance(got, dict) else {}

    def _write_lock_note(self, held: dict[str, Any]) -> None:
        """Say who holds this store. Never raises: the note is not the lock."""
        import json as _json

        note = {"pid": os.getpid(),
                "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                **{k: v for k, v in held.items() if v is not None}}
        try:
            self._lock_note.parent.mkdir(parents=True, exist_ok=True)
            self._lock_note.write_text(_json.dumps(note, indent=2) + "\n")
            self._wrote_lock_note = True
        except OSError:
            # A store directory we may not write to still serves. The note is a
            # better error message, not a requirement.
            pass

    def _clear_lock_note(self) -> None:
        if getattr(self, "_wrote_lock_note", False):
            with contextlib.suppress(OSError):
                self._lock_note.unlink(missing_ok=True)
            self._wrote_lock_note = False

    @staticmethod
    def _alive(pid: Any) -> bool:
        try:
            os.kill(int(pid), 0)
        except (OSError, TypeError, ValueError):
            return False
        return True

    def describe_holder(self) -> str:
        """One line naming whoever holds this store, or "" if nothing says.

        Read by the refusal, so it is the only place the note is trusted -- and
        even here it is only trusted to produce a sentence.
        """
        note = self._read_lock_note()
        if not note:
            return ""
        pid = note.get("pid")
        who = f"pid {pid}" if pid else "another process"
        if pid and not self._alive(pid):
            # It says a process that is gone. Worth saying so rather than
            # naming a pid somebody will look for and not find.
            return f"a note left by {who}, which is no longer running"
        where = note.get("serving")
        doors = note.get("doors") or []
        parts = [who]
        if where:
            parts.append(f"on {where}")
        if doors:
            parts.append(f"({', '.join(str(d) for d in doors)})")
        return " ".join(parts)

    def take_writer_lock(self, **held: Any) -> None:
        """Open the metadata file now, rather than on the first write.

        **The metadata file is the writer lock, and it was taken too late.** A
        parquet store is `tables/` plus `meta.duckdb`; DuckDB holds an exclusive
        lock on the second, and a read-only attach does not escape it. Every real
        write path reaches it before touching a file, because `open_job` calls
        `_meta()` -- so the lock already *is* the writer lock.

        What was wrong is the moment. `__init__` leaves it closed and `_meta()`
        opens it lazily, which is right for a reader and wrong for a program that
        is about to serve: two servers on one project with different ports both
        started, both answered reads, and **the second failed on its first write**
        -- an hour later, as a 500 on a page, with nothing naming the other
        process.

        A long-running program calls this while it is starting, so `StoreBusy`
        lands where somebody is still looking at the terminal. A reader must not
        call it: taking the lock is the one thing a parquet store exists to let a
        reader skip.

        On a DuckDB-file store the lock was taken in `__init__` and there is
        nothing left to do but say so in the note.

        **`held` is whatever the caller can say about itself** -- the address it
        serves on, the doors it carries, the project's name. It goes in
        `.qanat-lock` beside the store, so the next program to be refused can
        name the holder rather than name a locked file. Calling this again
        updates the note, which is how a server fills in its address after the
        port is known.
        """
        if self.kind == "parquet":
            try:
                self._meta()
            except StoreBusy as exc:
                who = self.describe_holder()
                if not who:
                    raise
                raise StoreBusy(f"{exc}\n  Held by {who}.\n"
                                "  One program holds the store at a time. Either stop it, or "
                                "give it\n  the door you wanted: a second door costs nothing, "
                                "a second program is refused.") from exc
        self._write_lock_note(held)

    # ---- reading while something else is writing ------------------------------
    @property
    def rcon(self):
        """A second connection, for readers that must not wait.

        A screen holds the writing connection for as long as it takes to walk the
        window, and an agent asking what the project holds has to be answerable
        while that happens -- otherwise the one moment you most want to watch is the
        one moment every read blocks. Each thread gets its own cursor onto the same
        database.

        It is also the safer place to read from: the as-of views live on the
        writer's search path, so a reader here always sees the real tables.
        """
        cur = getattr(self._readers, "cur", None)
        if cur is None:
            try:
                cur = self._readers.cur = self.con.cursor()
                # A fresh cursor starts on DuckDB's own default schema rather
                # than inheriting the writer's search path, so it is set here --
                # otherwise a bare table name read through this cursor resolves
                # against whatever the default happens to be.
                cur.execute(f"SET search_path='{self.home}'")
            except Exception:  # noqa: BLE001 -- no cursor support: share the one connection
                cur = self._readers.cur = self.con
        return cur

    # ---- meta ----------------------------------------------------------------
    @property
    def _meta_home(self) -> str:
        """Where the metadata tables are *created*, qualified.

        **Creating them bare put them in the wrong database.** A parquet store's
        own connection is `:memory:`, and the search path is `memory.main` then
        `meta.main` -- data first, because a bare table name means a data table.
        So `CREATE TABLE IF NOT EXISTS _qanat_runs` resolved to the front of that
        path and built the run log, the event log and the applied state **in
        memory**, in a file-backed store, where they were lost on every close.

        What that cost, on a parquet store only: `list_runs` came back empty
        after a restart, a subscription's last run read as never, and `plan`
        found no applied state -- so every job looked like it had never run and
        every pass reported the whole project as new.

        Reading was always right. The bare names in every query fall through
        the search path to `meta.main` once nothing shadows them in memory, so
        only the creation had to move.
        """
        return "meta.main." if (self.kind == "parquet" and self._meta_open) else ""

    def _ensure_meta(self) -> None:
        with self._lock:
            where = self._meta_home
            # One definition, for one exact datetime, and where it came from. A
            # null `schedule_id` is the ad-hoc path and it is first-class: a
            # hand-triggered ingest is a job with no schedule behind it, not the
            # absence of a job -- which is what makes its rows attributable.
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {where}{JOBS} (
                    job_id        BIGINT PRIMARY KEY,
                    definition_id VARCHAR,
                    kind          VARCHAR,
                    scheduled_for TIMESTAMP,
                    schedule_id   VARCHAR,
                    created_at    TIMESTAMP
                )""")
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {where}{EXECUTIONS} (
                    execution_id  BIGINT PRIMARY KEY,
                    job_id        BIGINT,
                    definition_id VARCHAR,
                    kind          VARCHAR,
                    attempt       INTEGER,
                    started_at    TIMESTAMP,
                    ended_at      TIMESTAMP,
                    status        VARCHAR,
                    rows_out      BIGINT,
                    targets       VARCHAR,
                    error         VARCHAR,
                    -- **The digest that actually ran**, stamped when the attempt
                    -- opens. Not looked up later: the version table holds one row
                    -- per definition and that row is whatever is applied *now*, so
                    -- after any edit every older row would report the new version.
                    definition_version VARCHAR
                )""")
            # A store written before the job layer existed has executions with no
            # job above them and no attempt number. Added rather than backfilled:
            # nothing can honestly say which attempt an old row was.
            for col, kind in (("execution_id", "BIGINT"), ("job_id", "BIGINT"),
                              ("definition_id", "VARCHAR"), ("attempt", "INTEGER"),
                              # Null on an execution that ran before this was
                              # recorded. Null is honest -- nothing can say which
                              # digest an old attempt used -- so `provenance`
                              # falls back to the lookup and says it did.
                              ("definition_version", "VARCHAR")):
                self.con.execute(
                    f"ALTER TABLE {where}{EXECUTIONS} ADD COLUMN IF NOT EXISTS {col} {kind}")
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {where}{EVENTS} (
                    ts     TIMESTAMP,
                    level  VARCHAR,
                    job_id VARCHAR,
                    message VARCHAR
                )""")
            # Who caused it. An agent over MCP, a person at the CLI, or the
            # clock -- the log is the only place a person can see that their
            # project changed under them while they were reading something else.
            # Rows written before this existed read as NULL, and stay
            # unattributed rather than being guessed at.
            self.con.execute(
                f"ALTER TABLE {where}{EVENTS} ADD COLUMN IF NOT EXISTS actor VARCHAR"
            )
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {where}{VERSIONS} (
                    definition_id VARCHAR PRIMARY KEY,
                    digest        VARCHAR,
                    spec          VARCHAR,
                    applied_at    TIMESTAMP
                )""")

    #: Written while a screen holds the store, cleared when it lets go. A screen
    #: rewrites the derived tables at each as-of date and puts them back in a
    #: `finally` -- which a SIGKILL, an OOM kill or a closed laptop never reaches.
    #: The store then reopened on tables truncated to a date in the past, with no
    #: sign that anything had happened.
    _SCREEN_MARK = "_screen_in_progress"
    #: What the same marker was called before the word changed. A store written
    #: by an older version still holds this spelling, and a store that reopens
    #: without noticing the marker leaves its derived tables truncated to a past
    #: date with nothing saying so. Read both, write only the new one.
    _SCREEN_MARK_WAS = "_replay_in_progress"

    def _recover(self) -> None:
        """Notice a screen that never finished, and put the store back.

        Sets `interrupted_screen` to the as-of date it died at. The derived tables
        are functions of raw, which a screen never touches, so one ordinary pass
        rebuilds them -- the `run` tool and `qanat schedule` do that on sight.
        """
        self.interrupted_screen: str | None = None
        with self._lock:
            row = self._one(
                f"SELECT spec FROM {self._meta_home}{VERSIONS} WHERE definition_id IN (?, ?) "
                f"ORDER BY definition_id LIMIT 1",
                [self._SCREEN_MARK, self._SCREEN_MARK_WAS],
            )
            stranded = self._scalar(
                f"SELECT count(*) FROM {self._meta_home}{EXECUTIONS} WHERE status = 'running'")
            if stranded:
                self.con.execute(
                    f"UPDATE {self._meta_home}{EXECUTIONS} SET status = 'interrupted', "
                    "ended_at = now()::TIMESTAMP, "
                    "error = 'the process ended before this execution did' WHERE status = 'running'"
                )
            if row is None:
                return
            self.con.execute(f'DROP SCHEMA IF EXISTS "{PIT}" CASCADE')
            self.con.execute(
                f"DELETE FROM {self._meta_home}{VERSIONS} WHERE definition_id IN (?, ?)",
                [self._SCREEN_MARK, self._SCREEN_MARK_WAS])
            self.interrupted_screen = str(row[0])
            self.event("warn", "store", (
                f"a screen was interrupted at as-of {row[0]}. Every table a transform writes was "
                "left holding only the rows that existed then -- run the pipeline once "
                "(the `run` tool) to rebuild them from raw."
            ))

    def screen_open(self, as_of: str) -> None:
        self._meta()
        with self._lock:
            self.con.execute(
                f"DELETE FROM {self._meta_home}{VERSIONS} WHERE definition_id = ?",
                [self._SCREEN_MARK])
            self.con.execute(
                f"INSERT INTO {self._meta_home}{VERSIONS} VALUES (?, 'screen', ?, now()::TIMESTAMP)",
                [self._SCREEN_MARK, str(as_of)],
            )

    def screen_closed(self) -> None:
        self._meta()
        with self._lock:
            self.con.execute(
                f"DELETE FROM {self._meta_home}{VERSIONS} WHERE definition_id = ?",
                [self._SCREEN_MARK])

    # ---- writing -------------------------------------------------------------
    def _stamp(self, ref: str, df: pd.DataFrame) -> pd.DataFrame:
        """Put the current execution on every row, and refuse a frame that set it.

        **A transform cannot write this column.** Provenance a script can set is
        provenance a script can get wrong, and a wrong answer about where a number
        came from is worse than no answer. The refusal names the column rather
        than dropping it silently, because a script that tried is a script whose
        author believed something that is not true.
        """
        theirs = [c for c in ENGINE_COLS if c in df.columns]
        if theirs:
            raise ValueError(
                f"{ref}: {', '.join(theirs)} is written by the engine, not by a job. "
                f"It names the execution that wrote the row, so a job setting it would be "
                f"attributing its own rows to whatever it chose. Drop the column"
            )
        if not len(df.columns):
            return df
        out = df.copy()
        out[EXEC_COL] = current_execution()
        out[SEQ_COL] = time.time_ns()
        # The execution is nullable, because a row written outside a job has none
        # and reads as unknown rather than as zero. The write counter never is:
        # every row was written at some point, and that is what orders them.
        return out.astype({EXEC_COL: "Int64", SEQ_COL: "int64"})

    def write(self, ref: str, df: pd.DataFrame, mode: str = "replace",
              key: Sequence[str] | None = None) -> int:
        """Land a dataframe as a table. Returns the row count written.

        `key` only applies to an append. An ingestion job polled hourly against a feed
        that answers with the last seven days appends those seven days every hour,
        and without a key the table is mostly copies. With one, a row that matches
        an existing key replaces it and the rest are added.
        """
        if df is None:
            return 0
        df = self._stamp(ref, df)
        name = phys(ref)
        if not len(df.columns) or len(df) == 0:
            # A transform with nothing to say returns an empty frame. That is an answer,
            # not a crash -- early in a screen there is often not enough history yet.
            #
            # It has to *clear* the table, though. Leaving the last pass's rows in
            # place would mean "I computed nothing" reads downstream as "here is a
            # fresh answer", and during a screen those rows are from the future.
            #
            # Clear rather than replace, because an empty frame carries no types.
            # Rebuilding the table from one turns every column into whatever pandas
            # defaults to, and the as-of view over it then cannot cast a timestamp
            # that has quietly become an integer.
            with self._lock:
                if self.kind == "parquet":
                    if self._exists(name):
                        # Empty the file, keeping its columns and their types.
                        self._publish(name, f"SELECT * FROM {self._q(name)} WHERE false")
                        return 0
                    if not len(df.columns):
                        return 0
                    self.con.register("_incoming", df)
                    try:
                        self._publish(name, "SELECT * FROM _incoming")
                    finally:
                        self._release()
                    return 0
                if self._exists(name):
                    self.con.execute(f"DELETE FROM {self._q(name)}")
                    return 0
                if not len(df.columns):
                    return 0
                # nothing to preserve yet: the first pass has to make the table,
                # types guessed or not
                self.con.register("_incoming", df)
                try:
                    self.con.execute(
                        f'CREATE OR REPLACE TABLE {self._q(name)} AS SELECT * FROM _incoming'
                    )
                finally:
                    self._release()
            return 0
        df = _typed(df)
        if self.kind == "parquet":
            return self._write_parquet(ref, df, mode, key)
        if key:
            missing = [k for k in key if k not in df.columns]
            if missing:
                raise ValueError(
                    f"{ref}: key names {', '.join(missing)}, which the rows do not have. "
                    f"They have {', '.join(map(str, df.columns))}"
                )
            # Two rows carrying the same key in one batch would both land, and the
            # key would stop meaning one row.
            df = df.drop_duplicates(subset=list(key), keep="last")
        written = len(df)
        with self._lock:
            self.con.register("_incoming", df)
            try:
                exists = self._exists(name)
                if mode == "append" and exists:
                    # By name, not by position. `INSERT ... SELECT *` lines the columns
                    # up in order, so a feed that reordered its columns wrote dates into
                    # the symbol column and reported ok.
                    have = [c for (c,) in self.con.execute(
                        "SELECT column_name FROM duckdb_columns() WHERE database_name = "
                        "current_catalog() AND schema_name = ? AND table_name = ? "
                        "ORDER BY column_index", [self.home, name]).fetchall()]
                    incoming = list(df.columns)
                    if set(have) != set(incoming):
                        added = sorted(set(incoming) - set(have))
                        gone = sorted(set(have) - set(incoming))
                        raise ValueError(
                            f"{ref}: the incoming rows do not match the table. "
                            + (f"New column(s): {', '.join(added)}. " if added else "")
                            + (f"Missing: {', '.join(gone)}. " if gone else "")
                            + f"The table holds: {', '.join(have)}"
                        )
                    self._widen(name, ref, have)
                    cols = ", ".join(f'"{_ident(c)}"' for c in have)
                    if key:
                        # **Nothing is deleted.** A row that arrives identical to
                        # one already stored is not stored twice; one that arrives
                        # with a changed value is appended *beside* the old row.
                        # This used to DELETE the matching key and insert the new
                        # row over it -- so a vendor restating yesterday's close
                        # silently replaced the number an answer had already
                        # rested on, and nothing anywhere said it had moved.
                        data = [c for c in have if c not in ENGINE_COLS]
                        same = ", ".join(f'"{_ident(c)}"' for c in data)
                        fresh = (f"SELECT {same} FROM _incoming "
                                 f"EXCEPT SELECT {same} FROM {self._q(name)}")
                        # Counted before the insert, or the answer is always zero.
                        written = int(self._scalar(
                            f"SELECT count(*) FROM ({fresh}) AS _new") or 0)
                        self.con.execute(
                            f'INSERT INTO {self._q(name)} '
                            f'({same}, "{_ident(EXEC_COL)}", "{_ident(SEQ_COL)}") '
                            f"SELECT {same}, CAST(? AS BIGINT), CAST(? AS BIGINT) "
                            f"FROM ({fresh}) AS _new",
                            [current_execution(), time.time_ns()],
                        )
                    else:
                        self.con.execute(
                            f'INSERT INTO {self._q(name)} ({cols}) '
                            f"SELECT {cols} FROM _incoming"
                        )
                else:
                    self.con.execute(
                        f'CREATE OR REPLACE TABLE {self._q(name)} AS SELECT * FROM _incoming'
                    )
            finally:
                self._release()
                if key:
                    self.remember_key(ref, key)
                self._now_view(ref)
                if self.as_of is not None:
                    self._shadow(ref)
        return written

    def _write_parquet(self, ref: str, df: pd.DataFrame, mode: str,
                       key: Sequence[str] | None) -> int:
        """Land a frame as parquet files in the table's directory.

        **An append only ever adds a file.** It used to rewrite the whole table
        when an incoming key was already present -- dropping the stored row and
        writing the new one in its place -- and that is the shape
        `data-once-served-is-never-replaced-automatically` refuses: a vendor
        restating yesterday's close silently replaced the number an answer had
        already rested on, with nothing anywhere saying it had moved.

        So a row identical to one already stored is not stored twice, and one
        that differs is appended beside it. Which of the two a read returns is
        the `now` view's answer, not this function's.
        """
        name = phys(ref)
        if key:
            missing = [k for k in key if k not in df.columns]
            if missing:
                raise ValueError(
                    f"{ref}: key names {', '.join(missing)}, which the rows do not have. "
                    f"They have {', '.join(map(str, df.columns))}"
                )
            # Two rows with one key in a single batch would both land, and the
            # key would stop meaning one row.
            df = df.drop_duplicates(subset=list(key), keep="last")
        written = len(df)
        with self._lock:
            self.con.register("_incoming", df)
            try:
                appending = mode == "append" and self._exists(name)
                if not appending:
                    self._publish(name, "SELECT * FROM _incoming")
                else:
                    # Checked before the path is chosen, not inside one of them.
                    # The cheap path skipped this once, and a frame with the
                    # wrong columns then landed as a file that `union_by_name`
                    # padded with nulls -- a wrong answer and no error, which is
                    # the failure this check exists to stop.
                    have = [c for (c,) in self.con.execute(
                        "SELECT column_name FROM duckdb_columns() WHERE database_name = "
                        "current_catalog() AND schema_name = ? AND table_name = ? "
                        "ORDER BY column_index", [self.home, name]).fetchall()]
                    incoming = list(df.columns)
                    if set(have) != set(incoming):
                        added = sorted(set(incoming) - set(have))
                        gone = sorted(set(have) - set(incoming))
                        raise ValueError(
                            f"{ref}: the incoming rows do not match the table. "
                            + (f"New column(s): {', '.join(added)}. " if added else "")
                            + (f"Missing: {', '.join(gone)}. " if gone else "")
                            + f"The table holds: {', '.join(have)}"
                        )
                    cols = ", ".join(f'"{_ident(c)}"' for c in have)

                    # `appending` is read once, above. Asking `_exists` again here
                    # would be true even when the branch above has just created the
                    # table, and this block would run with no columns worked out.
                    if not key or not self._overlaps(name, key, "_incoming"):
                        # The cheap path, and the common one. With no key there is
                        # nothing that could already be stored; with a key whose
                        # values are all new -- a day of prices after the last day
                        # stored -- there is nothing to compare against either. The
                        # footers settle it without reading a row, which is the
                        # reason a table is a directory.
                        self._add_part(name, f"SELECT {cols} FROM _incoming")
                    else:
                        # A key that is already here. Only the rows that actually
                        # differ go down, and they go down as a new file: nothing
                        # stored is read past its footer and nothing is rewritten.
                        data = [c for c in have if c not in ENGINE_COLS]
                        same = ", ".join(f'"{_ident(c)}"' for c in data)
                        fresh = (f"SELECT {same} FROM _incoming "
                                 f"EXCEPT SELECT {same} FROM {self._q(name)}")
                        written = int(self._scalar(
                            f"SELECT count(*) FROM ({fresh}) AS _new") or 0)
                        if written:
                            held = ("CAST(NULL AS BIGINT)" if current_execution() is None
                                    else f"CAST({int(current_execution())} AS BIGINT)")
                            self._add_part(name, (
                                f'SELECT {same}, {held} AS "{EXEC_COL}", '
                                f'CAST({time.time_ns()} AS BIGINT) AS "{SEQ_COL}" '
                                f"FROM ({fresh}) AS _new"))
            finally:
                self._release()
                if key:
                    self.remember_key(ref, key)
                self._now_view(ref)
                if self.as_of is not None:
                    self._shadow(ref)
        # What this job did, which is the rows it landed -- never the table
        # total, which made an incremental append of 16 rows report 11,672 and
        # look like it had re-fetched everything. The total is not counted here
        # at all now; see `_publish`.
        return written

    def count_older(self, ref: str, col: str, cutoff: Any) -> int:
        """How many rows `prune_rows` would drop. Reads, and removes nothing."""
        if not self.exists(ref):
            return 0
        with self._lock:
            return int(self._scalar(
                f'SELECT count(*) FROM {self._q(phys(ref))} '
                f'WHERE CAST("{_ident(col)}" AS TIMESTAMP) < ?', [cutoff]) or 0)

    def prune_rows(self, ref: str, col: str, cutoff: Any) -> int:
        """Drop rows whose `col` is older than `cutoff`. Returns how many went.

        A parquet file is not edited, so this is a rewrite of the rows that
        stay. A table is a DELETE. The caller does not have to know which.
        """
        name = phys(ref)
        where = f'CAST("{_ident(col)}" AS TIMESTAMP) < ?'
        with self._lock:
            before = self._scalar(f'SELECT count(*) FROM {self._q(name)}')
            if self.kind == "parquet":
                keep = (f'SELECT * FROM {self._q(name)} WHERE NOT ('
                        f'CAST("{_ident(col)}" AS TIMESTAMP) < TIMESTAMP \'{cutoff}\')')
                self._publish(name, keep)
                after = self._rows(name)
            else:
                self.con.execute(f'DELETE FROM {self._q(name)} WHERE {where}', [cutoff])
                after = self._scalar(f'SELECT count(*) FROM {self._q(name)}')
        return int(before - after)

    def _widen(self, name: str, ref: str, have: list[str]) -> None:
        """Make room for what is arriving, instead of rounding it away.

        A first poll of whole-number closes types the column BIGINT, and every later
        poll was then silently rounded -- 102.5 became 102, and a sub-dollar price
        became 0. Widening the column is the honest answer; where it cannot widen,
        the error names the column and both types rather than the cast.
        """
        theirs = {c: t for c, t in self.con.execute(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = "
            "current_catalog() AND schema_name = ? AND table_name = '_incoming'",
            [self.home]).fetchall()} or {
            c: t for c, t in self.con.execute("DESCRIBE SELECT * FROM _incoming").df()
            [["column_name", "column_type"]].to_numpy()}
        mine = {c: t for c, t in self.con.execute(
            "SELECT column_name, data_type FROM duckdb_columns() WHERE database_name = "
            "current_catalog() AND schema_name = ? AND table_name = ?",
            [self.home, name]).fetchall()}
        for col in have:
            old, new = mine.get(col), theirs.get(col)
            if not old or not new or old == new:
                continue
            if old in INT_TYPES and new in ("FLOAT", "DOUBLE", "DECIMAL"):
                self.con.execute(
                    f'ALTER TABLE {self._q(name)} ALTER COLUMN "{_ident(col)}" TYPE DOUBLE'
                )
                self.event("warn", "store", (
                    f"{ref}.{col} widened from {old} to DOUBLE -- the first rows it saw "
                    f"were whole numbers, and {new} values were being rounded into them"
                ))

    def sql_into(self, ref: str, select_sql: str) -> int:
        """Land a SELECT as a table, stamped with the execution that ran it.

        The stamp is wrapped around the query rather than asked of it, for the
        same reason `write` refuses the column: a `.sql` transform does not get
        to say where its rows came from.
        """
        name = phys(ref)
        execution = current_execution()
        held = "CAST(NULL AS BIGINT)" if execution is None else f"CAST({int(execution)} AS BIGINT)"
        stamped = (
            f'SELECT *, {held} AS "{EXEC_COL}", '
            f'CAST({time.time_ns()} AS BIGINT) AS "{SEQ_COL}" '
            f"FROM ({select_sql}) AS _out"
        )
        with self._lock:
            try:
                if self.kind == "parquet":
                    self._publish(name, stamped)
                    return self._rows(name)
                self.con.execute(f'CREATE OR REPLACE TABLE {self._q(name)} AS ({stamped})')
                return int(self._scalar(f'SELECT count(*) FROM {self._q(name)}'))
            finally:
                self._now_view(ref)
                if self.as_of is not None:
                    self._shadow(ref)

    def sql_append(self, ref: str, select_sql: str, key: Sequence[str]) -> int:
        """Land a SELECT as rows **added** to a table, never over it.

        What a materialised metric needs. A corrected price has to leave the
        value somebody was already shown readable, so the pass that notices the
        correction adds a row rather than rewriting the table -- and the row it
        adds is only the one that actually differs, so a recompute that finds
        nothing new writes nothing.

        Done in SQL rather than through a frame: a metric table is the first
        one whose size depends on a date grid rather than on how many filings
        exist, and 5,000 tickers over 250 trading days over 20 metrics is about
        25M rows a year -- measured at about 190 MB, whichever way they are
        split.
        """
        name = phys(ref)
        execution, written = current_execution(), 0
        held = "CAST(NULL AS BIGINT)" if execution is None else f"CAST({int(execution)} AS BIGINT)"
        with self._lock:
            try:
                if not self._exists(name):
                    stamped = (
                        f'SELECT *, {held} AS "{EXEC_COL}", '
                        f'CAST({time.time_ns()} AS BIGINT) AS "{SEQ_COL}" '
                        f"FROM ({select_sql}) AS _out"
                    )
                    if self.kind == "parquet":
                        self._publish(name, stamped)
                        written = self._rows(name)
                    else:
                        self.con.execute(
                            f"CREATE OR REPLACE TABLE {self._q(name)} AS ({stamped})")
                        written = int(self._scalar(
                            f"SELECT count(*) FROM {self._q(name)}") or 0)
                    return written

                have = [c for (c,) in self.con.execute(
                    "SELECT column_name FROM duckdb_columns() WHERE database_name = "
                    "current_catalog() AND schema_name = ? AND table_name = ? "
                    "ORDER BY column_index", [self.home, name]).fetchall()]
                data = [c for c in have if c not in ENGINE_COLS]
                same = ", ".join(f'"{_ident(c)}"' for c in data)
                fresh = (f"SELECT {same} FROM ({select_sql}) AS _out "
                         f"EXCEPT SELECT {same} FROM {self._q(name)}")
                written = int(self._scalar(
                    f"SELECT count(*) FROM ({fresh}) AS _new") or 0)
                if not written:
                    return 0
                rows = (f'SELECT {same}, {held} AS "{EXEC_COL}", '
                        f'CAST({time.time_ns()} AS BIGINT) AS "{SEQ_COL}" '
                        f"FROM ({fresh}) AS _new")
                if self.kind == "parquet":
                    self._add_part(name, rows)
                else:
                    self.con.execute(
                        f'INSERT INTO {self._q(name)} '
                        f'({same}, "{_ident(EXEC_COL)}", "{_ident(SEQ_COL)}") {rows}')
                return written
            finally:
                if key:
                    self.remember_key(ref, key)
                self._now_view(ref)
                if self.as_of is not None:
                    self._shadow(ref)

    def _q(self, name: str) -> str:
        """A table name pinned to the home schema, so `search_path` cannot move it."""
        return f'"{self.home}"."{_ident(name)}"'

    def readable(self, ref: str) -> str:
        """Where a read of this table comes from, as SQL.

        For a caller writing its own statement -- a join, an aggregate -- that
        has to respect the clock like any other read. `rows()` and `read()` use
        this for you; this is here for the query they cannot express.
        """
        return self._readable(ref)

    def _readable(self, ref: str) -> str:
        """Where a *read* comes from.

        Three layers, outermost first. While a screen is open that is the as-of
        view. Otherwise the `now` view, which keeps the engine's own columns out
        of a `SELECT *`. Otherwise the table. The pinning in `_q` exists so a
        write cannot be moved by `search_path`; applied to reads it also meant
        `store.read()` reached around the clock, which is the one thing the as-of
        views exist to prevent. Writes still use `_q`.
        """
        name = phys(ref)
        if self.as_of is not None and self._has_view(name):
            return f'"{PIT}"."{_ident(name)}"'
        if name in self._now:
            return f'"{NOW}"."{_ident(name)}"'
        return self._q(name)

    def _has_view(self, name: str) -> bool:
        try:
            return bool(self._scalar(
                "SELECT count(*) FROM duckdb_views() WHERE schema_name = ? AND view_name = ?",
                [PIT, name]))
        except Exception:  # noqa: BLE001 -- no view catalogue: fall back to the table
            return False

    # ---- which columns identify one row --------------------------------------
    def remember_key(self, ref: str, key: Sequence[str]) -> None:
        """Record what identifies one row in this table.

        **A reader needs this and cannot derive it.** Once a correction is a new
        row beside the old one rather than an edit, something has to say which of
        the two is current -- and the answer is "the newest write for this key",
        which is unanswerable without knowing the key. The writer knows it; this
        is where it leaves it.

        **It goes in a plain file rather than in the metadata database**, because
        the reader that needs it is exactly the one that must not take the writer
        lock. Kept in `meta.duckdb`, this was unreadable by a program that never
        opened it -- and such a program then returned every version of a
        corrected row with nothing saying so.
        """
        if not key:
            return
        name = phys(ref)
        with self._lock:
            if self._keys.get(name) == list(key):
                return
            self._keys[name] = list(key)
            self._write_keys()
        self._now_view(ref)

    def key_of(self, ref: str) -> list[str]:
        """What identifies one row in this table, or an empty list if nobody said."""
        return list(self._keys.get(phys(ref), []))

    @property
    def _keys_file(self) -> Path:
        """Beside the store, on the same rule as the lock note."""
        if self.kind == "parquet":
            return self.dir / KEYS_FILE
        return self.path.parent / f"{self.path.name}{KEYS_FILE}"

    def _write_keys(self) -> None:
        """Put the registry on disk. Called with `self._lock` held."""
        import json as _json

        try:
            self._keys_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._keys_file.with_suffix(self._keys_file.suffix + ".tmp")
            tmp.write_text(_json.dumps(
                {k: v for k, v in sorted(self._keys.items())}, indent=2) + "\n")
            # Renamed rather than written in place, so a reader globbing for it
            # mid-write finds the old file or the new one, never half of either.
            tmp.replace(self._keys_file)
        except OSError:
            # A read-only store still reads. Losing the registry would be worse
            # than not writing it, and the in-memory copy is already correct.
            pass

    def _load_keys(self) -> None:
        """Read the registry. **No lock, at any store kind, at open.**

        This is the whole point of the move. It used to live in `meta.duckdb`,
        which takes the writer lock -- so a reader could not have it, and
        `_current` silently stopped picking the newest row per key. A plain file
        is readable by anybody, so the keys are loaded here, in `__init__`,
        before anything has been asked.
        """
        import json as _json

        self._keys: dict[str, list[str]] = {}
        try:
            got = _json.loads(self._keys_file.read_text())
        except Exception:  # noqa: BLE001 -- absent on a new store, or pre-move
            self._migrate_keys()
            return
        if isinstance(got, dict):
            self._keys = {str(k): list(v) for k, v in got.items() if v}

    def _migrate_keys(self) -> None:
        """Lift the registry out of a store written before it had a file.

        Reachable only when the old table is: at a DuckDB store always, at a
        parquet store once something has opened the metadata file for its own
        reasons. **A parquet store whose file is absent and whose metadata is
        shut keeps today's behaviour and says so**, because the alternative is a
        reader taking the writer lock, which is the thing being fixed.
        """
        import json as _json

        if self.kind == "parquet" and not self._meta_open:
            return
        found: dict[str, list[str]] = {}
        with contextlib.suppress(Exception):
            for name, held in self.con.execute(
                    f"SELECT name, key_columns FROM {self._meta_home}{KEYS}").fetchall():
                found[name] = list(_json.loads(held))
        if found:
            self._keys = found
            self._write_keys()

    # ---- the `now` views -----------------------------------------------------
    def _engine_cols(self, name: str) -> list[str]:
        """Which engine-owned columns this table actually carries.

        A table written before the column existed carries none, and reads
        straight through. Nothing is backfilled: no honest value exists for a row
        whose execution was never recorded, and a guess there is worse than a
        null that reports itself as unknown.
        """
        try:
            have = {c for (c,) in self.con.execute(
                "SELECT column_name FROM duckdb_columns() WHERE database_name = "
                "current_catalog() AND schema_name = ? AND table_name = ?",
                [self.home, name]).fetchall()}
        except Exception:  # noqa: BLE001 -- no catalogue: assume none
            return []
        return [c for c in ENGINE_COLS if c in have]

    def _current(self, ref: str, inner: str) -> str:
        """One row per key, newest write wins, and the engine's columns hidden.

        **Two rows for one key is the shape a correction takes**: a re-fetch that
        finds a different value writes a new row and leaves the old one readable,
        because an answer somebody was already given cannot be lost. The cost the
        decision names is exactly this -- *every reader now has to pick* -- and
        the risk is not size but a reader that forgets to and silently returns
        two answers for one day. So the picking is here, in the view, once.

        Ordered by the execution that wrote the row. A row written before the
        engine recorded one sorts last: a corrected value from a real pass beats
        an unattributed one.
        """
        name = phys(ref)
        hidden = list(self._engine_cols(name))
        key = [k for k in self._keys.get(name, []) if k]
        if not key:
            return (f"SELECT * EXCLUDE ({', '.join(chr(34) + _ident(c) + chr(34) for c in hidden)}) "
                    f"FROM ({inner}) AS _t") if hidden else inner
        parts = ", ".join(f'"{_ident(k)}"' for k in key)
        order = f'"{_ident(SEQ_COL)}" DESC' if SEQ_COL in hidden else "1"
        drop = ", ".join(f'"{_ident(c)}"' for c in [*hidden, "_qanat_rn"])
        return (
            f"SELECT * EXCLUDE ({drop}) FROM ("
            f"SELECT *, row_number() OVER (PARTITION BY {parts} ORDER BY {order}) "
            f'AS "_qanat_rn" FROM ({inner}) AS _t) AS _r WHERE "_qanat_rn" = 1'
        )

    def _now_view(self, ref: str) -> bool:
        """(Re)build the `now` view over one table. True if it needs one."""
        name = phys(ref)
        mine = self._engine_cols(name)
        if not mine and not self._keys.get(name):
            self._now.discard(name)
            return False
        with contextlib.suppress(Exception):
            self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{NOW}"')
            self.con.execute(
                f'CREATE OR REPLACE VIEW "{NOW}"."{_ident(name)}" AS '
                + self._current(ref, f"SELECT * FROM {self._q(name)}")
            )
            first = not self._now
            self._now.add(name)
            # The first view puts this schema on the search path, and that is
            # load-bearing rather than tidy: a `.sql` transform names a table
            # bare, so without it the transform reads the physical table,
            # inherits the upstream's execution column, and the engine's own
            # stamp lands beside it as a second column with the same name --
            # after which the rows report whichever one the reader found first.
            if first:
                self.con.execute(f"SET search_path='{self._search_path()}'")
        return True

    def _build_now_views(self) -> None:
        """One pass at open, so a reader never pays for discovering these."""
        self._now: set[str] = getattr(self, "_now", set())
        for ref in self.all_tables():
            self._now_view(ref)

    # ---- reading -------------------------------------------------------------
    def read(self, ref: str, limit: int | None = None, as_of: str | None = None,
             time_column: str | None = None) -> pd.DataFrame:
        """Read a table. With `as_of`, only rows that existed at that timestamp.

        `time_column` is the project's own word for this table's clock, from
        `time_columns`. Without it only the default names are tried, so a table
        whose clock is declared in the project -- `raw.documents: published_at` --
        was refused as having none.
        """
        q = f'SELECT * FROM {self._readable(ref)}'
        params: list[Any] = []
        if as_of:
            col = self.time_column(ref, time_column)
            if col is None:
                raise LookupError(
                    f"{ref} has no time column, so it cannot be read as of a timestamp. "
                    f"Name one of {', '.join(TIME_COLS)}, or set time_columns in qanat.yaml"
                )
            q += f' WHERE {self._instant(ref, col)} <= CAST(? AS TIMESTAMP)'
            params.append(as_of)
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return self.con.execute(q, params).df()

    def rows(self, ref: str, where: str = "", params: Sequence[Any] = (),
             columns: str = "*", order_by: str = "", limit: int | None = None,
             select_params: Sequence[Any] = ()) -> pd.DataFrame:
        """Part of a table, filtered in the database instead of afterwards.

        `read()` hands back the whole table, which is right when the caller wants
        the whole table and wrong the rest of the time. **This is the one a reader
        wants when it already knows which rows it is after.** The predicate goes
        to DuckDB, which drops the row groups whose min/max cannot match, and only
        the answer crosses into Python.

        The difference is not small and it grows with the corpus. Measured at
        724,500 spans: the whole table into Python is 1,101 ms, and one document's
        rows through here is 3.7 ms.

        `columns` narrows the read as well. A tool that never looks at `text` has
        no reason to carry it, and on a filings corpus `text` is nearly all of the
        bytes.

        **Values are bound, never interpolated.** `select_params` binds the `?` in
        `columns`, which the database reads before the ones in `where`.
        """
        if not self.exists(ref):
            raise LookupError(f"{ref} has not been written yet")
        q = f"SELECT {columns} FROM {self._readable(ref)}"
        if where:
            q += f" WHERE {where}"
        if order_by:
            q += f" ORDER BY {order_by}"
        if limit is not None:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return self.con.execute(q, [*select_params, *params]).df()

    def _instant(self, ref: str, col: str) -> str:
        """`as_instant` for one real column, with its declared type looked up."""
        info = self.table_info(ref)
        dtype = dict(info.columns).get(col) if info else None
        return as_instant(col, dtype)

    def time_column(self, ref: str, override: str | None = None) -> str | None:
        """Which column carries this table's timestamp."""
        info = self.table_info(ref)
        if not info:
            return None
        lower = {c.lower(): c for c, _ in info.columns}
        if override:
            return lower.get(override.lower())
        for cand in TIME_COLS:
            if cand in lower:
                return lower[cand]
        return None

    # ---- point in time -------------------------------------------------------
    def open_pit(self, as_of: str, overrides: dict[str, str] | None = None) -> list[str]:
        """Shadow every table with a view holding only the rows that existed at
        `as_of`, and put that schema first on the search path.

        A transform reads the past without knowing it is being screened, and
        one that forgot to filter cannot see the future by accident.
        """
        overrides = overrides or {}
        self._pit_overrides = overrides
        shadowed: list[str] = []
        with self._lock:
            self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{PIT}"')
            self.as_of = as_of
            for ref in self.all_tables():
                if self._shadow(ref):
                    shadowed.append(ref)
            self.con.execute(f"SET search_path='{self._search_path()}'")
        return shadowed

    def _shadow(self, ref: str) -> bool:
        """(Re)build one as-of view. True if the table has a clock to filter on.

        Built over the `now` view when there is one, so a screened read gets the
        same columns as an ordinary read -- otherwise a transform would see the
        engine's own column under a screen and not outside one, which is the
        worst of the three possibilities.
        """
        col = self.time_column(ref, self._pit_overrides.get(ref))
        name = phys(ref)
        # **The clock first, then the pick.** Built on the `now` view instead, a
        # screen would ask "which row is current today" and then filter the
        # answer by date -- so a value corrected last week would be invisible at
        # a date before the correction, rather than showing what was knowable
        # then.
        if col is None:
            # no clock on this table: it is the same at every as-of
            inner = f"SELECT * FROM {self._q(name)}"
        else:
            inner = (
                f'SELECT * FROM {self._q(name)} '
                f"WHERE {self._instant(ref, col)} <= CAST('{self.as_of}' AS TIMESTAMP)"
            )
        body = self._current(ref, inner)
        self.con.execute(f'CREATE OR REPLACE VIEW "{PIT}"."{name}" AS {body}')
        return col is not None

    def close_pit(self) -> None:
        with self._lock:
            # Cleared first, so the path this builds no longer has PIT on it.
            self.as_of = None
            self.con.execute(f"SET search_path='{self._search_path()}'")
            try:
                self.con.execute(f'DROP SCHEMA IF EXISTS "{PIT}" CASCADE')
            except Exception as exc:  # noqa: BLE001 -- a leftover view schema is harmless
                self.con.execute(
                    f"INSERT INTO {self._meta_home}{EVENTS} VALUES (now()::TIMESTAMP, 'warn', 'screen', ?)",
                    [f"could not drop the as-of views: {exc}"],
                )
        self.as_of = None

    def max_time(self, ref: str, override: str | None = None) -> str | None:
        """The newest timestamp in a table, or None if it has no clock."""
        col = self.time_column(ref, override)
        if col is None or not self.exists(ref):
            return None
        with self._lock:
            v = self._scalar(
                f'SELECT max({self._instant(ref, col)}) FROM {self._q(phys(ref))}')
        return str(v) if v is not None else None

    def time_range(self, ref: str, override: str | None = None) -> tuple[str, str] | None:
        """The oldest and newest timestamps in a table, or None with no clock.

        `max_time` answers what an ingestion job should ask for next. This answers what
        a point-in-time read can be asked for at all: before the oldest row
        there was nothing public, so a screen as of then has no corpus to read.
        """
        col = self.time_column(ref, override)
        if col is None or not self.exists(ref):
            return None
        with self._lock:
            lo, hi = self._one(
                f'SELECT min({self._instant(ref, col)}), max({self._instant(ref, col)}) '
                f'FROM {self._q(phys(ref))}') or (None, None)
        return None if lo is None or hi is None else (str(lo), str(hi))

    def query(self, sql: str, params: Sequence[Any] | None = None) -> pd.DataFrame:
        """Run a statement. `params` are bound by the database, never pasted in.

        A caller with a value in hand -- a document id, a ticker, a date -- passes
        it here rather than formatting it into the string. The reading tools take
        those values from an agent, so an id that happens to carry a quote has to
        stay an id rather than becoming a statement.
        """
        with self._lock:
            return self.con.execute(sql, list(params) if params else []).df()

    def _exists(self, name: str) -> bool:
        if self.kind == "parquet":
            # The files are the table. The view over them is a detail that can
            # be rebuilt, so the files are what get asked.
            return bool(self._parts(name))
        return bool(self._scalar(
            "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_catalog() "
            "AND schema_name = ? AND table_name = ?",
            [self.home, name]))

    def exists(self, ref: str) -> bool:
        with self._lock:
            return self._exists(phys(ref))

    def all_tables(self) -> list[str]:
        """Every table actually in the file, as `stage.table`. Meta tables excluded."""
        if self.kind == "parquet":
            root = self.dir / TABLES_DIR
            names = sorted(d.name for d in (root.iterdir() if root.is_dir() else [])
                           if d.is_dir() and any(d.glob("*.parquet")))
            return [unphys(n) for n in names if n not in META and "__" in n]
        rows = self.rcon.execute(
            "SELECT table_name FROM duckdb_tables() WHERE database_name = current_catalog() "
            "AND schema_name = ? ORDER BY table_name",
            [self.home],
        ).fetchall()
        return [unphys(n) for (n,) in rows if n not in META and "__" in n]

    def drop(self, ref: str) -> int:
        """Remove a table. Returns the row count it had."""
        info = self.table_info(ref)
        with self._lock:
            if self.kind == "parquet":
                self.con.execute(f'DROP VIEW IF EXISTS {self._q(phys(ref))}')
                for part in self._parts(phys(ref)):
                    part.unlink(missing_ok=True)
                with contextlib.suppress(OSError):
                    self._pq(phys(ref)).rmdir()
            else:
                self.con.execute(f'DROP TABLE IF EXISTS {self._q(phys(ref))}')
        return info.rows if info else 0

    # ---- definition versions: what a definition looked like when it last ran --
    def load_state(self) -> dict[str, dict[str, str]]:
        self._meta()
        with self._lock:
            rows = self.con.execute(
                f"SELECT definition_id, digest, spec FROM {self._meta_home}{VERSIONS}").fetchall()
        skip = {self._SCREEN_MARK, self._SCREEN_MARK_WAS}
        return {j: {"digest": d, "spec": s} for j, d, s in rows if j not in skip}

    def set_state(self, definition_id: str, digest: str, spec: str) -> None:
        """Record one definition as applied, when it actually succeeds."""
        self._meta()
        with self._lock:
            self.con.execute(
                f"DELETE FROM {self._meta_home}{VERSIONS} WHERE definition_id = ?",
                [definition_id])
            self.con.execute(
                f"INSERT INTO {self._meta_home}{VERSIONS} VALUES (?, ?, ?, now()::TIMESTAMP)",
                [definition_id, digest, spec])

    def max_time_written_by(self, ref: str, execution_id: int,
                            override: str | None = None) -> Any | None:
        """The newest clock value among the rows **one execution** wrote.

        `max_time` answers it of the whole table, which is the wrong question
        for a lookahead check: a keyed table is never rewritten, so a table
        filled by an ordinary run keeps those rows forever -- and every later
        as-of run would then be accused of writing the dates the first one did.
        Measured on the leak battery: a metric table filled normally failed
        every subsequent screened run.
        """
        col = self.time_column(ref, override)
        if col is None or not self.exists(ref):
            return None
        name = phys(ref)
        if EXEC_COL not in self._engine_cols(name):
            return None
        with self._lock:
            row = self._one(
                f'SELECT max("{_ident(col)}") FROM {self._q(name)} '
                f'WHERE CAST("{EXEC_COL}" AS BIGINT) = ?',
                [int(execution_id)], con=self.rcon)
        return None if row is None else row[0]

    def raw_ref(self, ref: str) -> str:
        """The table under the `now` view, **engine columns included**.

        `readable` hands back the view, which hides `_qanat_execution` from
        `SELECT *` on purpose -- a `.sql` transform naming a table bare would
        otherwise inherit its input's stamp and get a second one of its own. A
        reader that wants to *filter* on the execution has to see it, and
        pinning a metric version is exactly that: the rows wanted are the ones a
        particular execution wrote, including ones the view treats as
        superseded.
        """
        return self._q(phys(ref))

    def executions_ref(self) -> str:
        """The executions table, qualified -- for a reader that needs to join it.

        A screen pinning a version joins here rather than being handed the
        digest per row: the digest lives on the attempt, not on the row, so one
        execution's worth of rows all resolve through one entry.
        """
        self._meta()
        return f"{self._meta_home}{EXECUTIONS}"

    def definition_version(self, definition_id: str) -> dict[str, Any] | None:
        """The applied digest for one definition, which is its version.

        What `lineage` resolves the last link of the chain to: a row names an
        execution, the execution names a job, the job names a definition, and
        this names which version of it was running.
        """
        self._meta()
        with self._lock:
            row = self._one(
                f"SELECT definition_id, digest, applied_at FROM {self._meta_home}{VERSIONS} "
                f"WHERE definition_id = ?", [definition_id], con=self.rcon)
        if row is None:
            return None
        return {"definition": row[0], "version": row[1], "applied_at": str(row[2])}

    def forget_state(self, definition_ids: list[str]) -> None:
        self._meta()
        with self._lock:
            for did in definition_ids:
                self.con.execute(
                    f"DELETE FROM {self._meta_home}{VERSIONS} WHERE definition_id = ?", [did])

    def save_state(self, entries: dict[str, tuple[str, str]]) -> None:
        """entries: id -> (digest, spec json). Replaces the whole snapshot."""
        self._meta()
        with self._lock:
            self.con.execute(f"DELETE FROM {self._meta_home}{VERSIONS}")
            for did, (digest, spec) in entries.items():
                self.con.execute(
                    f"INSERT INTO {self._meta_home}{VERSIONS} VALUES (?, ?, ?, now()::TIMESTAMP)",
                    [did, digest, spec]
                )

    def table_info(self, ref: str) -> TableInfo | None:
        name = phys(ref)
        r = self.rcon
        cols = r.execute(
            "SELECT column_name, data_type FROM duckdb_columns() "
            "WHERE database_name = current_catalog() AND schema_name = ? AND table_name = ? "
            "ORDER BY column_index",
            [self.home, name],
        ).fetchall()
        if not cols:
            return None
        # The engine's own columns are not the table's shape. `describe_table`
        # answers what a caller may read, and `lineage` is what reads this one.
        cols = [(c, ty) for c, ty in cols if c not in ENGINE_COLS]
        rows = self._scalar(f'SELECT count(*) FROM {self._q(name)}', con=r)
        # When this table was last written is in the run log, and `table_info` is
        # on the read path -- `read()` reaches it through `time_column`. Opening
        # the metadata file here would mean every reader took the one writer lock
        # on it, which is the thing a parquet store exists to avoid. A reader
        # gets no timestamp instead.
        last = None
        if self.kind != "parquet" or self._meta_open:
            last = self._scalar(
                f"SELECT max(ended_at) FROM {self._meta_home}{EXECUTIONS} WHERE targets LIKE ? AND status = 'ok'",
                [f"%{ref}%"], con=r)
        stage, _, tname = ref.partition(".")
        return TableInfo(ref, stage, tname, rows, [(c, t) for c, t in cols],
                         str(last) if last else None)

    def profile(self, ref: str) -> dict[str, Any] | None:
        """What is actually in a table, column by column: how much of it is filled,
        how many distinct values, and the range it spans.

        Computed as aggregates rather than by reading the table. "How many symbols,
        over what dates, with how many holes" is the first question anyone asks of a
        new table, and answering it by pulling rows works on a demo and falls over
        on a real panel -- five hundred names by ten years is a million rows to move
        so somebody can learn there are five hundred names. This costs a scan and
        the answer is the same size whatever the table is.
        """
        info = self.table_info(ref)
        if info is None:
            return None
        name = phys(ref)
        cols = list(info.columns)

        def query(with_range: bool) -> Any:
            parts = ["count(*) AS n"]
            for i, (col, _t) in enumerate(cols):
                q = f'"{_ident(col)}"'
                parts.append(f"count({q}) AS f{i}")
                parts.append(f"count(DISTINCT {q}) AS d{i}")
                if with_range:
                    parts.append(f"CAST(min({q}) AS VARCHAR) AS lo{i}")
                    parts.append(f"CAST(max({q}) AS VARCHAR) AS hi{i}")
            return self._one(
                f'SELECT {", ".join(parts)} FROM {self._q(name)}', con=self.rcon)

        ranged = True
        try:
            row = query(True)
        except Exception:  # noqa: BLE001 -- a type min/max cannot order, e.g. a blob
            ranged = False
            row = query(False)

        per = 4 if ranged else 2
        total = int(row[0] or 0)
        out = []
        for i, (col, typ) in enumerate(cols):
            base = 1 + i * per
            filled = int(row[base] or 0)
            out.append({
                "name": col,
                "type": typ,
                "filled": filled,
                "nulls": total - filled,
                # A table with no rows is not 0% filled, it is unanswerable. Saying
                # 0 there would draw an empty bar next to a column that may be fine.
                "fill": (filled / total) if total else None,
                "distinct": int(row[base + 1] or 0),
                "min": row[base + 2] if ranged else None,
                "max": row[base + 3] if ranged else None,
            })
        return {
            "ref": ref, "rows": total, "updated_at": info.updated_at,
            "time_column": self.time_column(ref),
            "columns": out,
        }

    # ---- jobs, executions and events -----------------------------------------
    def open_job(self, definition_id: str, kind: str, scheduled_for: Any,
                 schedule_id: str | None = None) -> int:
        """The job for this definition at this instant, opening one if there is none.

        **Idempotent on (definition, instant), and that is the whole point of the
        layer.** Re-running a failed job adds an execution to the job that is
        already there; it does not create a second one. Otherwise three attempts
        at the 07:00 poll are three unrelated rows and nothing can answer *"did
        the 07:00 poll succeed?"*
        """
        self._meta()
        with self._lock:
            row = self._one(
                f"SELECT job_id FROM {self._meta_home}{JOBS} WHERE definition_id = ? "
                f"AND scheduled_for = ? LIMIT 1", [definition_id, scheduled_for],
                con=self.rcon)
            if row is not None:
                return int(row[0])
            job_id = time.time_ns() // 1000
            self.con.execute(
                f"INSERT INTO {self._meta_home}{JOBS} VALUES (?, ?, ?, ?, ?, now()::TIMESTAMP)",
                [job_id, definition_id, kind, scheduled_for, schedule_id],
            )
        return job_id

    def start_execution(self, job_id: int, definition_id: str, kind: str,
                        targets: list[str], definition_version: str | None = None) -> int:
        """One attempt at one job. `attempt` counts from 1, per job.

        **It also opens the attribution window**: every write from here until
        `end_execution` carries this id. Set here rather than by each caller
        because the runner has four exit paths and three of them are error paths
        -- a caller that has to remember is a caller that will forget on the one
        path nobody tests.

        `definition_version` is the digest of the definition as it is about to
        run, and it is **recorded on the attempt rather than resolved from it.**
        The version table keeps one row per definition, holding what is applied
        now, so a lookup answers "what would run today" -- which for a row
        written before an edit is the wrong question and a confident wrong
        answer. Recorded on failure too: what ran is what ran.
        """
        self._meta()
        execution_id = time.time_ns() // 1000
        EXECUTION.set(execution_id)
        with self._lock:
            attempt = int(self._scalar(
                f"SELECT count(*) FROM {self._meta_home}{EXECUTIONS} WHERE job_id = ?",
                [job_id], con=self.rcon) or 0) + 1
            self.con.execute(
                f"INSERT INTO {self._meta_home}{EXECUTIONS} VALUES "
                f"(?, ?, ?, ?, ?, now()::TIMESTAMP, NULL, 'running', 0, ?, NULL, ?)",
                [execution_id, job_id, definition_id, kind, attempt, ",".join(targets),
                 definition_version],
            )
        return execution_id

    def end_execution(self, execution_id: int, status: str, rows: int = 0,
                      error: str | None = None) -> None:
        """Close one attempt, and the attribution window it opened.

        Cleared here so a write that happens after the attempt is over is
        attributed to nothing rather than to the last job that ran -- which is
        the wrong answer, and the one a leftover value would give.
        """
        self._meta()
        EXECUTION.set(None)
        with self._lock:
            self.con.execute(
                f"UPDATE {self._meta_home}{EXECUTIONS} SET ended_at = now()::TIMESTAMP, "
                f"status = ?, rows_out = ?, error = ? WHERE execution_id = ?",
                [status, rows, error, execution_id],
            )

    def job(self, job_id: int) -> dict[str, Any] | None:
        """One job and every attempt at it, newest attempt last.

        `status` is the job's, derived rather than stored: a job one attempt
        succeeded at succeeded, however many failed first. That is the question
        the layer exists to answer.
        """
        self._meta()
        row = self._one(
            f"SELECT definition_id, kind, scheduled_for, schedule_id FROM "
            f"{self._meta_home}{JOBS} WHERE job_id = ?", [job_id], con=self.rcon)
        if row is None:
            return None
        df = self.rcon.execute(
            f"SELECT execution_id, attempt, started_at, ended_at, status, rows_out, error "
            f"FROM {self._meta_home}{EXECUTIONS} WHERE job_id = ? ORDER BY attempt",
            [job_id]).df()
        attempts = _records(df)
        return {
            "job_id": int(job_id),
            "definition": row[0],
            "kind": row[1],
            "scheduled_for": str(row[2]),
            "schedule": row[3],
            "status": "ok" if any(a["status"] == "ok" for a in attempts)
                      else (attempts[-1]["status"] if attempts else "running"),
            "attempts": attempts,
        }

    def provenance(self, ref: str, where: str = "", params: Sequence[Any] = (),
                   limit: int = 20) -> dict[str, Any]:
        """Which executions wrote the rows this filter matches, resolved up the chain.

        **The question is "where did this number come from", and until now there
        was nothing to ask it of.** Each execution resolves to its job, the
        definition behind that job, and which version of the definition was
        running -- four hops from a row to a reason.

        Rows written before the column existed come back under `unknown`, counted
        rather than hidden. A silent zero there would read as "nothing to say"
        when the truth is "nobody recorded it".
        """
        name = phys(ref)
        if not self.exists(ref) or EXEC_COL not in self._engine_cols(name):
            return {"table": ref, "executions": [], "unknown_rows": 0,
                    "note": "this table carries no provenance column: every row in it "
                            "was written before the engine recorded one"}
        clause = f" WHERE {where}" if where else ""
        rows = self.rcon.execute(
            f'SELECT "{EXEC_COL}" AS e, count(*) AS n FROM {self._q(name)}{clause} '
            f'GROUP BY 1 ORDER BY n DESC', list(params)).fetchall()
        unknown = sum(int(n) for e, n in rows if e is None)
        out = []
        for execution_id, n in [(e, n) for e, n in rows if e is not None][:int(limit)]:
            found = self.execution(int(execution_id)) or {}
            job = self.job(found["job_id"]) if found.get("job_id") is not None else None
            # The digest this attempt recorded, which is the one that ran. Only
            # an attempt from before the column existed falls back to the
            # lookup, and it is labelled so nobody reads "today's" as "then's".
            ran = found.get("definition_version")
            if not ran:
                ran = ((self.definition_version(found.get("definition") or "") or {})
                       .get("version"))
                ran = f"{ran} (current, not recorded)" if ran else None
            out.append({
                "rows": int(n),
                "execution": int(execution_id),
                "attempt": found.get("attempt"),
                "status": found.get("status"),
                "job": found.get("job_id"),
                "scheduled_for": (job or {}).get("scheduled_for"),
                "schedule": (job or {}).get("schedule"),
                "definition": found.get("definition"),
                "definition_version": ran,
            })
        return {
            "table": ref,
            "executions": out,
            "unknown_rows": unknown,
            "note": (
                f"{unknown:,} row(s) name no execution -- written before the engine "
                f"recorded one, which is unknown rather than none" if unknown else None
            ),
        }

    def execution(self, execution_id: int) -> dict[str, Any] | None:
        """One attempt, and the job it was an attempt at."""
        self._meta()
        row = self._one(
            f"SELECT job_id, definition_id, kind, attempt, started_at, ended_at, status, "
            f"rows_out, targets, error, definition_version "
            f"FROM {self._meta_home}{EXECUTIONS} "
            f"WHERE execution_id = ?", [execution_id], con=self.rcon)
        if row is None:
            return None
        return {
            "execution_id": int(execution_id), "job_id": None if row[0] is None else int(row[0]),
            "definition": row[1], "kind": row[2],
            "attempt": None if row[3] is None else int(row[3]),
            "started_at": str(row[4]), "ended_at": None if row[5] is None else str(row[5]),
            "status": row[6], "rows": None if row[7] is None else int(row[7]),
            "targets": [x for x in str(row[8] or "").split(",") if x], "error": row[9],
            # The digest that ran, or None on an attempt from before this was
            # recorded. Never the current one dressed up as it.
            "definition_version": row[10],
        }

    def bar_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Every time the bar moved, and who moved it.

        Read off the event log rather than kept separately, because that is where
        `actor` already lives -- so a change made by an agent mid-answer is
        attributable without the agent having to remember to say so.
        """
        self._meta()
        df = self.rcon.execute(f"""
            SELECT ts, level, message, actor FROM {self._meta_home}{EVENTS}
            WHERE job_id = 'bar' ORDER BY ts DESC LIMIT {int(limit)}
        """).df()
        return _records(df)

    def event(self, level: str, job_id: str, message: str) -> None:
        self._meta()
        with self._lock:
            # Named columns, not positional: `actor` was added to this table after
            # the fact, and a bare VALUES list would put the message in it.
            self.con.execute(
                f"INSERT INTO {self._meta_home}{EVENTS} (ts, level, job_id, message, actor) "
                f"VALUES (now()::TIMESTAMP, ?, ?, ?, ?)",
                [level, job_id, message, current_actor()],
            )

    def recent_executions(self, limit: int = 50) -> list[dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(
            f"SELECT * FROM {self._meta_home}{EXECUTIONS} "
            f"ORDER BY started_at DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def last_execution_by_definition(self) -> dict[str, dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(f"""
                SELECT e.* FROM {self._meta_home}{EXECUTIONS} e
                JOIN (SELECT definition_id, max(started_at) AS s
                      FROM {self._meta_home}{EXECUTIONS} GROUP BY definition_id) m
                  ON e.definition_id = m.definition_id AND e.started_at = m.s
        """).df()
        return {r["definition_id"]: r for r in _records(df)}

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(
            f"SELECT * FROM {self._meta_home}{EVENTS} ORDER BY ts DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def close(self) -> None:
        # The note goes first: a clean exit must not leave a file saying this
        # process holds a store it is about to let go of.
        self._clear_lock_note()
        with self._lock:
            self.con.close()
            if self.kind == "parquet":
                _release_duckdb(self._shared_key)


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    out = []
    for rec in df.to_dict("records"):
        clean = {}
        for k, v in rec.items():
            if pd.isna(v):
                clean[k] = None
            elif hasattr(v, "isoformat"):
                clean[k] = v.isoformat(sep=" ", timespec="seconds")
            elif hasattr(v, "item"):
                clean[k] = v.item()
            else:
                clean[k] = v
        out.append(clean)
    return out
