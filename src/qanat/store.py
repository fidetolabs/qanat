"""Where the tables live: the run history, the event log, and the data itself.

DuckDB is the engine -- it is what executes a `.sql` step -- and the storage is
one of two kinds, with the SQL a step writes identical across both:

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
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
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

RUNS = "_qanat_runs"
EVENTS = "_qanat_events"
STATE = "_qanat_state"
META = (RUNS, EVENTS, STATE)

#: Schema holding the as-of views a screen reads through. Named, not `asof`,
#: because DuckDB reserves that word for ASOF JOIN.
PIT = "qanat_pit"

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


def current_actor() -> str:
    return ACTOR.get()


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

    `ctx.read` refuses a table the step did not declare, and says why. A `.sql` body
    could reach the same table anyway, so the edge the graph drew was not where the
    data came from, `stale()` never marked the consumer, and `check` warned that a
    table read by two steps "is never read by anything".
    """
    return {f"{a}.{b}" for a, b in _TABLE_TOKEN.findall(_SQL_NOISE.sub(" ", sql or ""))}


def _typed(df: pd.DataFrame) -> pd.DataFrame:
    """Land a column that is entirely null as text, not as a guess.

    A field that is optional in a feed -- a US holiday's `counties`, a symbol on a
    payload row -- is null in the first response that happens not to carry it, and
    pandas types that as INTEGER. The type sticks, and the day the field arrives with
    real data every poll fails on a cast that names neither the feed nor the field.
    Text is the one type that does not close the door; a step can cast it later.
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
                    "    · reach it through the process that already has it: `qanat mcp --http`\n"
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
        self._readers = threading.local()
        if self.kind == "parquet":
            # Needs `home`, so it waits until the connection is settled.
            self._publish_views()
        else:
            self._ensure_meta()
            self._recover()

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
        self._migrate_single_files(root)
        for d in sorted(root.iterdir() if root.is_dir() else []):
            if d.is_dir():
                self._view(d.name)

    def _migrate_single_files(self, root: Path) -> None:
        """Move a table written as one file into its own directory.

        0.6 wrote `facts.parquet`. 0.7 writes `facts/0000.parquet`. Doing it on
        open means an existing project keeps working without being told to run
        anything.
        """
        for f in sorted(root.glob("*.parquet")) if root.is_dir() else []:
            d = root / f.stem
            d.mkdir(exist_ok=True)
            f.replace(d / "0000.parquet")

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
        is open -- that is what makes a step read the past without knowing it.
        Then the data. Then the metadata catalogue, when a parquet store has
        opened one.

        The bug this exists to stop: attaching the metadata file reset the
        search path, which silently took the as-of views off the front. Every
        step then read the live table and the lookahead guard caught it as a
        leak, which is the right failure for the wrong reason.
        """
        parts = []
        if self.as_of is not None:
            parts.append(PIT)
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
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {where}{RUNS} (
                    run_id     BIGINT PRIMARY KEY,
                    job_id     VARCHAR,
                    kind       VARCHAR,
                    started_at TIMESTAMP,
                    ended_at   TIMESTAMP,
                    status     VARCHAR,
                    rows_out   BIGINT,
                    targets    VARCHAR,
                    error      VARCHAR
                )""")
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
                CREATE TABLE IF NOT EXISTS {where}{STATE} (
                    job_id     VARCHAR PRIMARY KEY,
                    digest     VARCHAR,
                    spec       VARCHAR,
                    applied_at TIMESTAMP
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
                f"SELECT spec FROM {self._meta_home}{STATE} WHERE job_id IN (?, ?) ORDER BY job_id LIMIT 1",
                [self._SCREEN_MARK, self._SCREEN_MARK_WAS],
            )
            stranded = self._scalar(
                f"SELECT count(*) FROM {self._meta_home}{RUNS} WHERE status = 'running'")
            if stranded:
                self.con.execute(
                    f"UPDATE {self._meta_home}{RUNS} SET status = 'interrupted', ended_at = now()::TIMESTAMP, "
                    "error = 'the process ended before this run did' WHERE status = 'running'"
                )
            if row is None:
                return
            self.con.execute(f'DROP SCHEMA IF EXISTS "{PIT}" CASCADE')
            self.con.execute(f"DELETE FROM {self._meta_home}{STATE} WHERE job_id IN (?, ?)",
                             [self._SCREEN_MARK, self._SCREEN_MARK_WAS])
            self.interrupted_screen = str(row[0])
            self.event("warn", "store", (
                f"a screen was interrupted at as-of {row[0]}. Every table a step writes was "
                "left holding only the rows that existed then -- run the pipeline once "
                "(the `run` tool) to rebuild them from raw."
            ))

    def screen_open(self, as_of: str) -> None:
        self._meta()
        with self._lock:
            self.con.execute(f"DELETE FROM {self._meta_home}{STATE} WHERE job_id = ?", [self._SCREEN_MARK])
            self.con.execute(
                f"INSERT INTO {self._meta_home}{STATE} VALUES (?, 'screen', ?, now()::TIMESTAMP)",
                [self._SCREEN_MARK, str(as_of)],
            )

    def screen_closed(self) -> None:
        self._meta()
        with self._lock:
            self.con.execute(f"DELETE FROM {self._meta_home}{STATE} WHERE job_id = ?", [self._SCREEN_MARK])

    # ---- writing -------------------------------------------------------------
    def write(self, ref: str, df: pd.DataFrame, mode: str = "replace",
              key: Sequence[str] | None = None) -> int:
        """Land a dataframe as a table. Returns the row count written.

        `key` only applies to an append. A source polled every hour against a feed
        that answers with the last seven days appends those seven days every hour,
        and without a key the table is mostly copies. With one, a row that matches
        an existing key replaces it and the rest are added.
        """
        if df is None:
            return 0
        name = phys(ref)
        if not len(df.columns) or len(df) == 0:
            # A step with nothing to say returns an empty frame. That is an answer,
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
        with self._lock:
            self.con.register("_incoming", df)
            try:
                exists = self._exists(name)
                if mode == "append" and exists:
                    if key:
                        # IS NOT DISTINCT FROM, not `=`: in SQL a null never equals a
                        # null, so a row whose key held a blank matched nothing and was
                        # appended again on every single poll, for ever.
                        match = " AND ".join(
                            f'{self._q(name)}."{_ident(k)}" IS NOT DISTINCT FROM '
                            f'_incoming."{_ident(k)}"' for k in key
                        )
                        self.con.execute(
                            f'DELETE FROM {self._q(name)} USING _incoming WHERE {match}'
                        )
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
                    self.con.execute(
                        f'INSERT INTO {self._q(name)} ({cols}) SELECT {cols} FROM _incoming'
                    )
                else:
                    self.con.execute(
                        f'CREATE OR REPLACE TABLE {self._q(name)} AS SELECT * FROM _incoming'
                    )
            finally:
                self._release()
                if self.as_of is not None:
                    self._shadow(ref)
        return len(df)

    def _write_parquet(self, ref: str, df: pd.DataFrame, mode: str,
                       key: Sequence[str] | None) -> int:
        """Land a frame as one parquet file, written whole and renamed into place.

        A parquet file is not edited, so an append is read-concat-write rather
        than an INSERT. At the size a filings corpus reaches that is the right
        trade: the corpus is written once a day by one recorder and read all
        day by everybody, and a rewrite nobody waits for beats an in-place
        write that blocks every reader.
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
                if appending and (not key or not self._overlaps(name, key, "_incoming")):
                    # The cheap path, and the common one. With no key there is
                    # nothing to replace; with a key whose values are all new --
                    # a day of prices after the last day stored -- there is
                    # nothing to replace either. One file goes down and the rest
                    # of the table is not read, let alone rewritten.
                    self._add_part(name, f"SELECT {cols} FROM _incoming")
                elif appending:
                    kept = f"SELECT {cols} FROM {self._q(name)}"
                    if key:
                        # A null never equals a null in SQL, so a key holding a
                        # blank would match nothing and the row would be appended
                        # again on every poll, for ever.
                        match = " AND ".join(
                            f'old."{_ident(k)}" IS NOT DISTINCT FROM _incoming."{_ident(k)}"'
                            for k in key
                        )
                        kept = (f"SELECT {cols} FROM {self._q(name)} old WHERE NOT EXISTS "
                                f"(SELECT 1 FROM _incoming WHERE {match})")
                    # The overlapping path: a key that already exists has to be
                    # replaced, and a parquet file cannot be edited, so the table
                    # is rewritten. Reached only when the footers say an incoming
                    # key really could be in a file already here.
                    #
                    # By name, not by position: a feed that reordered its columns
                    # would otherwise write dates into the symbol column.
                    self._publish(
                        name, f"{kept} UNION ALL BY NAME SELECT {cols} FROM _incoming")
            finally:
                self._release()
                if self.as_of is not None:
                    self._shadow(ref)
        # What this job did, which is the rows it handed over -- never the table
        # total, which made an incremental append of 16 rows report 11,672 and
        # look like it had re-fetched everything. The total is not counted here
        # at all now; see `_publish`.
        return len(df)

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
        name = phys(ref)
        with self._lock:
            try:
                if self.kind == "parquet":
                    self._publish(name, select_sql)
                    return self._rows(name)
                self.con.execute(f'CREATE OR REPLACE TABLE {self._q(name)} AS ({select_sql})')
                return int(self._scalar(f'SELECT count(*) FROM {self._q(name)}'))
            finally:
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

        While a screen is open that is the as-of view, not the table under it. The
        pinning in `_q` exists so a write cannot be moved by `search_path`; applied
        to reads it also meant `store.read()` reached around the clock, which is
        the one thing the as-of views exist to prevent. Writes still use `_q`.
        """
        name = phys(ref)
        if self.as_of is not None and self._has_view(name):
            return f'"{PIT}"."{_ident(name)}"'
        return self._q(name)

    def _has_view(self, name: str) -> bool:
        try:
            return bool(self._scalar(
                "SELECT count(*) FROM duckdb_views() WHERE schema_name = ? AND view_name = ?",
                [PIT, name]))
        except Exception:  # noqa: BLE001 -- no view catalogue: fall back to the table
            return False

    # ---- reading -------------------------------------------------------------
    def read(self, ref: str, limit: int | None = None, as_of: str | None = None) -> pd.DataFrame:
        """Read a table. With `as_of`, only rows that existed at that timestamp."""
        q = f'SELECT * FROM {self._readable(ref)}'
        params: list[Any] = []
        if as_of:
            col = self.time_column(ref)
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

        A step therefore reads the past without knowing it is being screened, and
        a step that forgot to filter cannot see the future by accident.
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
        """(Re)build one as-of view. True if the table has a clock to filter on."""
        col = self.time_column(ref, self._pit_overrides.get(ref))
        name = phys(ref)
        if col is None:
            # no clock on this table: it is the same at every as-of
            body = f"SELECT * FROM {self._q(name)}"
        else:
            body = (
                f'SELECT * FROM {self._q(name)} '
                f"WHERE {self._instant(ref, col)} <= CAST('{self.as_of}' AS TIMESTAMP)"
            )
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

        `max_time` answers what a source should ask for next. This answers what
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

    # ---- applied state: what the file looked like the last time it ran --------
    def load_state(self) -> dict[str, dict[str, str]]:
        self._meta()
        with self._lock:
            rows = self.con.execute(f"SELECT job_id, digest, spec FROM {self._meta_home}{STATE}").fetchall()
        skip = {self._SCREEN_MARK, self._SCREEN_MARK_WAS}
        return {j: {"digest": d, "spec": s} for j, d, s in rows if j not in skip}

    def set_state(self, job_id: str, digest: str, spec: str) -> None:
        """Record one job as applied. Called when that job actually succeeds."""
        self._meta()
        with self._lock:
            self.con.execute(f"DELETE FROM {self._meta_home}{STATE} WHERE job_id = ?", [job_id])
            self.con.execute(f"INSERT INTO {self._meta_home}{STATE} VALUES (?, ?, ?, now()::TIMESTAMP)", [job_id, digest, spec])

    def forget_state(self, job_ids: list[str]) -> None:
        self._meta()
        with self._lock:
            for job_id in job_ids:
                self.con.execute(f"DELETE FROM {self._meta_home}{STATE} WHERE job_id = ?", [job_id])

    def save_state(self, entries: dict[str, tuple[str, str]]) -> None:
        """entries: job_id -> (digest, spec json). Replaces the whole snapshot."""
        self._meta()
        with self._lock:
            self.con.execute(f"DELETE FROM {self._meta_home}{STATE}")
            for job_id, (digest, spec) in entries.items():
                self.con.execute(
                    f"INSERT INTO {self._meta_home}{STATE} VALUES (?, ?, ?, now()::TIMESTAMP)", [job_id, digest, spec]
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
        rows = self._scalar(f'SELECT count(*) FROM {self._q(name)}', con=r)
        # When this table was last written is in the run log, and `table_info` is
        # on the read path -- `read()` reaches it through `time_column`. Opening
        # the metadata file here would mean every reader took the one writer lock
        # on it, which is the thing a parquet store exists to avoid. A reader
        # gets no timestamp instead.
        last = None
        if self.kind != "parquet" or self._meta_open:
            last = self._scalar(
                f"SELECT max(ended_at) FROM {self._meta_home}{RUNS} WHERE targets LIKE ? AND status = 'ok'",
                [f"%{ref}%"], con=r)
        stage, _, tname = ref.partition(".")
        return TableInfo(ref, stage, tname, rows, [(c, t) for c, t in cols],
                         str(last) if last else None)

    def profile(self, ref: str) -> dict[str, Any] | None:
        """What is actually in a table, column by column: how much of it is filled,
        how many distinct values, and the range it spans.

        Computed as aggregates rather than by reading the table. "How many symbols,
        over what dates, with how many holes" is the first question anyone asks of a
        new source, and answering it by pulling rows works on a demo and falls over
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

    # ---- runs and events -----------------------------------------------------
    def start_run(self, job_id: str, kind: str, targets: list[str]) -> int:
        self._meta()
        run_id = time.time_ns() // 1000
        with self._lock:
            self.con.execute(
                f"INSERT INTO {self._meta_home}{RUNS} VALUES (?, ?, ?, now()::TIMESTAMP, NULL, 'running', 0, ?, NULL)",
                [run_id, job_id, kind, ",".join(targets)],
            )
        return run_id

    def end_run(self, run_id: int, status: str, rows: int = 0, error: str | None = None) -> None:
        self._meta()
        with self._lock:
            self.con.execute(
                f"UPDATE {self._meta_home}{RUNS} SET ended_at = now()::TIMESTAMP, status = ?, rows_out = ?, error = ? "
                "WHERE run_id = ?",
                [status, rows, error, run_id],
            )

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

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(
            f"SELECT * FROM {self._meta_home}{RUNS} ORDER BY started_at DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def last_run_by_job(self) -> dict[str, dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(f"""
                SELECT r.* FROM {self._meta_home}{RUNS} r
                JOIN (SELECT job_id, max(started_at) AS s FROM {self._meta_home}{RUNS} GROUP BY job_id) m
                  ON r.job_id = m.job_id AND r.started_at = m.s
        """).df()
        return {r["job_id"]: r for r in _records(df)}

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        self._meta()
        df = self.rcon.execute(
            f"SELECT * FROM {self._meta_home}{EVENTS} ORDER BY ts DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def close(self) -> None:
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
