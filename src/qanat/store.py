"""Where the tables live: the run history, the event log, and the data itself.

DuckDB is always the engine -- it is what executes a `.sql` step -- but it does
not have to be the storage. Point `store:` at a path and the tables live in one
local file; point it at a Postgres DSN and DuckDB attaches that server and the
tables are created there instead. The SQL a step writes is identical either way.

A table lives under `<stage>__<name>`, so the stage a table belongs to is
readable from its name alone -- in the console, in the DuckDB CLI, and in
anything else that opens the database.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

PG_SCHEMES = ("postgresql://", "postgres://")


class StoreBusy(RuntimeError):
    """The database is open somewhere else. Says which door to use instead."""

RUNS = "_qanat_runs"
EVENTS = "_qanat_events"
STATE = "_qanat_state"
BACKTESTS = "_qanat_backtests"
BT_WEIGHTS = "_qanat_bt_weights"
BT_PERIODS = "_qanat_bt_periods"
META = (RUNS, EVENTS, STATE, BACKTESTS, BT_WEIGHTS, BT_PERIODS)

#: Schema holding the as-of views a replay reads through. Named, not `asof`,
#: because DuckDB reserves that word for ASOF JOIN.
PIT = "qanat_pit"

#: Column names a table's timestamp may go by, most specific first.
TIME_COLS = ("as_of", "ts", "timestamp", "datetime", "date", "time", "fetched_at",
             "created_at", "updated_at")


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


def is_postgres(url: str) -> bool:
    return str(url).startswith(PG_SCHEMES)


class Store:
    """One database, whether that is a local file or a Postgres server."""

    def __init__(self, target: str | Path):
        from qanat.sources.rest import expand  # ${PGPASSWORD} etc, never in the file

        self.target = str(target)
        self._lock = threading.RLock()

        if is_postgres(self.target):
            dsn = expand(self.target)
            self.kind = "postgres"
            self.path = None
            self.label = dsn.split("@")[-1] if "@" in dsn else dsn
            self.con = duckdb.connect()
            self.con.execute("INSTALL postgres; LOAD postgres;")
            # ATTACH takes no bind parameters, so the dsn is inlined and quoted
            self.con.execute(f"ATTACH '{dsn.replace(chr(39), chr(39) * 2)}' AS qanat (TYPE postgres)")
            self.con.execute("USE qanat")
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
                    "  A DuckDB file takes one writer at a time, so the console, the CLI and an\n"
                    "  agent cannot each open it. Pick one door:\n"
                    "    · stop `qanat serve`, then run this again, or\n"
                    "    · keep the console and drive it from there, or\n"
                    "    · start `qanat mcp` first and ask the agent to open_console. One process\n"
                    "      serves both, or\n"
                    "    · move the project to Postgres (`qanat init --postgres`), which takes many"
                ) from exc
        self.home = self.con.execute("SELECT current_schema()").fetchone()[0]
        self.as_of: str | None = None
        self._pit_overrides: dict[str, str] = {}
        self._readers = threading.local()
        self._ensure_meta()

    def __str__(self) -> str:
        return f"{self.kind}: {self.label}"

    # ---- reading while something else is writing ------------------------------
    @property
    def rcon(self):
        """A second connection, for readers that must not wait.

        A replay holds the writing connection for as long as it takes to walk the
        window, and the console has to keep drawing while that happens -- otherwise
        the one moment you most want to watch is the one moment the page goes blank.
        Each thread gets its own cursor onto the same database.

        It is also the safer place to read from: the as-of views live on the
        writer's search path, so a reader here always sees the real tables.
        """
        cur = getattr(self._readers, "cur", None)
        if cur is None:
            try:
                cur = self._readers.cur = self.con.cursor()
                # A fresh cursor starts on DuckDB's own default schema, not the
                # writer's. Against a DuckDB file those are the same and nothing
                # shows; against an attached Postgres the meta tables live in the
                # attached schema and every bare name here stops resolving.
                cur.execute(f"SET search_path='{self.home}'")
            except Exception:  # noqa: BLE001 -- no cursor support: share the one connection
                cur = self._readers.cur = self.con
        return cur

    # ---- meta ----------------------------------------------------------------
    def _ensure_meta(self) -> None:
        with self._lock:
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {RUNS} (
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
                CREATE TABLE IF NOT EXISTS {EVENTS} (
                    ts     TIMESTAMP,
                    level  VARCHAR,
                    job_id VARCHAR,
                    message VARCHAR
                )""")
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {BACKTESTS} (
                    run_id      BIGINT PRIMARY KEY,
                    created_at  TIMESTAMP,
                    from_date   VARCHAR,
                    to_date     VARCHAR,
                    rebalance   VARCHAR,
                    seed        BIGINT,
                    digest      VARCHAR,
                    status      VARCHAR,
                    periods     BIGINT,
                    gross       DOUBLE,
                    fees        DOUBLE,
                    slippage    DOUBLE,
                    net         DOUBLE,
                    turnover    DOUBLE,
                    report      VARCHAR,
                    error       VARCHAR
                )""")
            # Older databases predate the alpha column. Adding it is safe and keeps
            # every backtest anyone already ran, which is the point of the book.
            self.con.execute(
                f"ALTER TABLE {BACKTESTS} ADD COLUMN IF NOT EXISTS alpha VARCHAR"
            )
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {BT_WEIGHTS} (
                    run_id  BIGINT,
                    as_of   TIMESTAMP,
                    symbol  VARCHAR,
                    weight  DOUBLE
                )""")
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {BT_PERIODS} (
                    run_id      BIGINT,
                    as_of       TIMESTAMP,
                    priced_from TIMESTAMP,
                    priced_to   TIMESTAMP,
                    holdings    BIGINT,
                    gross       DOUBLE,
                    turnover    DOUBLE,
                    fees        DOUBLE,
                    slippage    DOUBLE,
                    net         DOUBLE
                )""")
            # No DEFAULT: when the store is an attached Postgres, DuckDB refuses a
            # DEFAULT in ALTER TABLE. Rows written since carry an explicit value,
            # and rows from before live scoring existed read as NULL, which `WHERE
            # live` excludes -- which is what they are.
            self.con.execute(
                f"ALTER TABLE {BACKTESTS} ADD COLUMN IF NOT EXISTS live BOOLEAN"
            )
            self.con.execute(f"""
                CREATE TABLE IF NOT EXISTS {STATE} (
                    job_id     VARCHAR PRIMARY KEY,
                    digest     VARCHAR,
                    spec       VARCHAR,
                    applied_at TIMESTAMP
                )""")

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
            # not a crash -- early in a replay there is often not enough history yet.
            #
            # It has to *clear* the table, though. Leaving the last pass's rows in
            # place would mean "I computed nothing" reads downstream as "here is a
            # fresh answer", and during a replay those rows are from the future.
            #
            # Clear rather than replace, because an empty frame carries no types.
            # Rebuilding the table from one turns every column into whatever pandas
            # defaults to, and the as-of view over it then cannot cast a timestamp
            # that has quietly become an integer.
            with self._lock:
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
                    self.con.unregister("_incoming")
            return 0
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
            self._unshadow(name)
            try:
                exists = self._exists(name)
                if mode == "append" and exists:
                    if key:
                        match = " AND ".join(
                            f'{self._q(name)}."{k}" = _incoming."{k}"' for k in key
                        )
                        self.con.execute(
                            f'DELETE FROM {self._q(name)} USING _incoming WHERE {match}'
                        )
                    self.con.execute(f'INSERT INTO {self._q(name)} SELECT * FROM _incoming')
                else:
                    self.con.execute(
                        f'CREATE OR REPLACE TABLE {self._q(name)} AS SELECT * FROM _incoming'
                    )
            finally:
                self.con.unregister("_incoming")
                if self.as_of is not None:
                    self._shadow(ref)
        return len(df)

    def sql_into(self, ref: str, select_sql: str) -> int:
        name = phys(ref)
        with self._lock:
            self._unshadow(name)
            try:
                self.con.execute(f'CREATE OR REPLACE TABLE {self._q(name)} AS ({select_sql})')
                return self.con.execute(f'SELECT count(*) FROM {self._q(name)}').fetchone()[0]
            finally:
                if self.as_of is not None:
                    self._shadow(ref)

    def _q(self, name: str) -> str:
        """A table name pinned to the home schema, so `search_path` cannot move it."""
        return f'"{self.home}"."{name}"' 

    # ---- reading -------------------------------------------------------------
    def read(self, ref: str, limit: int | None = None, as_of: str | None = None) -> pd.DataFrame:
        """Read a table. With `as_of`, only rows that existed at that timestamp."""
        name = phys(ref)
        q = f'SELECT * FROM {self._q(name)}'
        params: list[Any] = []
        if as_of:
            col = self.time_column(ref)
            if col is None:
                raise LookupError(
                    f"{ref} has no time column, so it cannot be read as of a timestamp. "
                    f"Name one of {', '.join(TIME_COLS)}, or set time_columns in qanat.yaml"
                )
            q += f' WHERE CAST("{col}" AS TIMESTAMP) <= CAST(? AS TIMESTAMP)'
            params.append(as_of)
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            return self.con.execute(q, params).df()

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

        A step therefore reads the past without knowing it is being replayed, and
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
            self.con.execute(f"SET search_path='{PIT},{self.home}'")
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
                f"WHERE CAST(\"{col}\" AS TIMESTAMP) <= CAST('{self.as_of}' AS TIMESTAMP)"
            )
        self.con.execute(f'CREATE OR REPLACE VIEW "{PIT}"."{name}" AS {body}')
        return col is not None

    def _unshadow(self, name: str) -> None:
        """Take the as-of view off a table so the table itself can be replaced.

        Postgres refuses to drop a table a view depends on, and replacing a table
        is a drop. DuckDB allows it, which is why this only ever showed up against
        a real server.
        """
        if self.as_of is None:
            return
        self.con.execute(f'DROP VIEW IF EXISTS "{PIT}"."{name}"')

    def close_pit(self) -> None:
        with self._lock:
            self.con.execute(f"SET search_path='{self.home}'")
            try:
                self.con.execute(f'DROP SCHEMA IF EXISTS "{PIT}" CASCADE')
            except Exception as exc:  # noqa: BLE001 -- a leftover view schema is harmless
                self.con.execute(
                    f"INSERT INTO {EVENTS} VALUES (now()::TIMESTAMP, 'warn', 'replay', ?)",
                    [f"could not drop the as-of views: {exc}"],
                )
        self.as_of = None

    def max_time(self, ref: str, override: str | None = None) -> str | None:
        """The newest timestamp in a table, or None if it has no clock."""
        col = self.time_column(ref, override)
        if col is None or not self.exists(ref):
            return None
        with self._lock:
            v = self.con.execute(
                f'SELECT max(CAST("{col}" AS TIMESTAMP)) FROM {self._q(phys(ref))}'
            ).fetchone()[0]
        return str(v) if v is not None else None

    def query(self, sql: str) -> pd.DataFrame:
        with self._lock:
            return self.con.execute(sql).df()

    def _exists(self, name: str) -> bool:
        return bool(
            self.con.execute(
                "SELECT count(*) FROM duckdb_tables() WHERE database_name = current_catalog() "
                "AND schema_name = ? AND table_name = ?",
                [self.home, name],
            ).fetchone()[0]
        )

    def exists(self, ref: str) -> bool:
        with self._lock:
            return self._exists(phys(ref))

    def all_tables(self) -> list[str]:
        """Every table actually in the file, as `stage.table`. Meta tables excluded."""
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
            self.con.execute(f'DROP TABLE IF EXISTS {self._q(phys(ref))}')
        return info.rows if info else 0

    # ---- applied state: what the file looked like the last time it ran --------
    def load_state(self) -> dict[str, dict[str, str]]:
        with self._lock:
            rows = self.con.execute(f"SELECT job_id, digest, spec FROM {STATE}").fetchall()
        return {j: {"digest": d, "spec": s} for j, d, s in rows}

    def set_state(self, job_id: str, digest: str, spec: str) -> None:
        """Record one job as applied. Called when that job actually succeeds."""
        with self._lock:
            self.con.execute(f"DELETE FROM {STATE} WHERE job_id = ?", [job_id])
            self.con.execute(f"INSERT INTO {STATE} VALUES (?, ?, ?, now()::TIMESTAMP)", [job_id, digest, spec])

    def forget_state(self, job_ids: list[str]) -> None:
        with self._lock:
            for job_id in job_ids:
                self.con.execute(f"DELETE FROM {STATE} WHERE job_id = ?", [job_id])

    def save_state(self, entries: dict[str, tuple[str, str]]) -> None:
        """entries: job_id -> (digest, spec json). Replaces the whole snapshot."""
        with self._lock:
            self.con.execute(f"DELETE FROM {STATE}")
            for job_id, (digest, spec) in entries.items():
                self.con.execute(
                    f"INSERT INTO {STATE} VALUES (?, ?, ?, now()::TIMESTAMP)", [job_id, digest, spec]
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
        rows = r.execute(f'SELECT count(*) FROM {self._q(name)}').fetchone()[0]
        last = r.execute(
            f"SELECT max(ended_at) FROM {RUNS} WHERE targets LIKE ? AND status = 'ok'",
            [f"%{ref}%"],
        ).fetchone()[0]
        stage, _, tname = ref.partition(".")
        return TableInfo(ref, stage, tname, rows, [(c, t) for c, t in cols],
                         str(last) if last else None)

    # ---- runs and events -----------------------------------------------------
    def start_run(self, job_id: str, kind: str, targets: list[str]) -> int:
        run_id = time.time_ns() // 1000
        with self._lock:
            self.con.execute(
                f"INSERT INTO {RUNS} VALUES (?, ?, ?, now()::TIMESTAMP, NULL, 'running', 0, ?, NULL)",
                [run_id, job_id, kind, ",".join(targets)],
            )
        return run_id

    def end_run(self, run_id: int, status: str, rows: int = 0, error: str | None = None) -> None:
        with self._lock:
            self.con.execute(
                f"UPDATE {RUNS} SET ended_at = now()::TIMESTAMP, status = ?, rows_out = ?, error = ? "
                "WHERE run_id = ?",
                [status, rows, error, run_id],
            )

    # ---- backtests -----------------------------------------------------------
    def last_live_backtest(self) -> str | None:
        """The `to` date of the newest live run that finished, or None."""
        row = self.rcon.execute(
            f"SELECT to_date FROM {BACKTESTS} WHERE live AND status = 'ok' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return str(row[0]) if row else None

    def start_backtest(
        self, frm: str, to: str, rebalance: str, seed: int, digest: str, alpha: str = "",
        live: bool = False,
    ) -> int:
        run_id = time.time_ns() // 1000
        with self._lock:
            self.con.execute(
                f"INSERT INTO {BACKTESTS} (run_id, created_at, from_date, to_date, rebalance, "
                "seed, digest, status, periods, alpha, live) "
                "VALUES (?, now()::TIMESTAMP, ?, ?, ?, ?, ?, 'running', 0, ?, ?)",
                [run_id, frm, to, rebalance, seed, digest, alpha, live],
            )
        return run_id

    def end_backtest(
        self, run_id: int, status: str, totals: dict[str, Any] | None = None,
        report: str | None = None, error: str | None = None,
    ) -> None:
        t = totals or {}
        with self._lock:
            self.con.execute(
                f"UPDATE {BACKTESTS} SET status = ?, periods = ?, gross = ?, fees = ?, "
                "slippage = ?, net = ?, turnover = ?, report = ?, error = ? WHERE run_id = ?",
                [status, t.get("periods", 0), t.get("gross"), t.get("fees"), t.get("slippage"),
                 t.get("net"), t.get("turnover"), report, error, run_id],
            )

    def save_bt_weights(self, run_id: int, as_of: str, df: pd.DataFrame) -> int:
        """Keep the portfolio this replay held at one as-of date."""
        if df is None or df.empty:
            return 0
        keep = df.rename(columns={c: c.lower() for c in df.columns})
        if not {"symbol", "weight"} <= set(keep.columns):
            return 0
        keep = keep[["symbol", "weight"]].copy()
        keep.insert(0, "as_of", pd.Timestamp(as_of))
        keep.insert(0, "run_id", run_id)
        with self._lock:
            self.con.register("_bt_incoming", keep)
            try:
                self.con.execute(f"INSERT INTO {BT_WEIGHTS} SELECT * FROM _bt_incoming")
            finally:
                self.con.unregister("_bt_incoming")
        return len(keep)

    def save_bt_period(self, run_id: int, period: Any) -> None:
        """One closed holding period, written the moment it closes -- so the PnL is
        queryable while the replay is still running, not only after it ends."""
        with self._lock:
            self.con.execute(
                f"INSERT INTO {BT_PERIODS} VALUES (?, CAST(? AS TIMESTAMP), CAST(? AS TIMESTAMP), "
                "CAST(? AS TIMESTAMP), ?, ?, ?, ?, ?, ?)",
                [run_id, period.as_of, period.priced_from, period.priced_to, period.holdings,
                 period.gross, period.turnover, period.fees, period.slippage, period.net],
            )

    def bt_periods(self, run_id: int) -> list[dict[str, Any]]:
        df = self.rcon.execute(
            f"SELECT * FROM {BT_PERIODS} WHERE run_id = ? ORDER BY as_of", [run_id]
        ).df()
        return _records(df)

    def backtests(self, limit: int = 50) -> list[dict[str, Any]]:
        df = self.rcon.execute(
                f"SELECT run_id, created_at, from_date, to_date, rebalance, seed, digest, "
                f"status, periods, gross, fees, slippage, net, turnover, error, alpha, live "
                f"FROM {BACKTESTS} ORDER BY created_at DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def alpha_book(self) -> list[dict[str, Any]]:
        """Every alpha this project has ever backtested, newest run first.

        The book is derived from history rather than declared anywhere: an alpha is
        in it because it produced a result, which is the only claim worth keeping.
        """
        with self._lock:
            df = self.con.execute(f"""
                SELECT alpha,
                       count(*)                                  AS runs,
                       max(created_at)                           AS last_run,
                       arg_max(run_id, created_at)               AS last_run_id,
                       arg_max(net, created_at)                  AS last_net,
                       max(net)                                  AS best_net
                FROM {BACKTESTS}
                WHERE alpha IS NOT NULL AND alpha <> '' AND periods > 0
                GROUP BY alpha
                ORDER BY max(created_at) DESC
            """).df()
        return _records(df)

    def backtest(self, run_id: int) -> dict[str, Any] | None:
        df = self.rcon.execute(f"SELECT * FROM {BACKTESTS} WHERE run_id = ?", [run_id]).df()
        rows = _records(df)
        return rows[0] if rows else None

    def bt_weights(self, run_id: int, as_of: str | None = None) -> list[dict[str, Any]]:
        q = f"SELECT as_of, symbol, weight FROM {BT_WEIGHTS} WHERE run_id = ?"
        params: list[Any] = [run_id]
        if as_of:
            q += " AND CAST(as_of AS TIMESTAMP) = CAST(? AS TIMESTAMP)"
            params.append(as_of)
        df = self.rcon.execute(q + " ORDER BY as_of, symbol", params).df()
        return _records(df)

    def event(self, level: str, job_id: str, message: str) -> None:
        with self._lock:
            self.con.execute(
                f"INSERT INTO {EVENTS} VALUES (now()::TIMESTAMP, ?, ?, ?)", [level, job_id, message]
            )

    def recent_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        df = self.rcon.execute(
            f"SELECT * FROM {RUNS} ORDER BY started_at DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def last_run_by_job(self) -> dict[str, dict[str, Any]]:
        df = self.rcon.execute(f"""
                SELECT r.* FROM {RUNS} r
                JOIN (SELECT job_id, max(started_at) AS s FROM {RUNS} GROUP BY job_id) m
                  ON r.job_id = m.job_id AND r.started_at = m.s
        """).df()
        return {r["job_id"]: r for r in _records(df)}

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        df = self.rcon.execute(
            f"SELECT * FROM {EVENTS} ORDER BY ts DESC LIMIT {int(limit)}"
        ).df()
        return _records(df)

    def close(self) -> None:
        with self._lock:
            self.con.close()


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
