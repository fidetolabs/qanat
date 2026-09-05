"""The Postgres store, which DuckDB reaches by attaching the server.

Every other test in this suite uses a DuckDB file, and that hid four bugs: a
DEFAULT in ALTER TABLE, reader cursors landing on the wrong schema, as-of views
blocking the table they shadow, and a cast DuckDB allows and Postgres refuses.
None of them could show up against a file, because a file is one catalog and
DuckDB is the permissive end of both engines.

These run against a real server and are skipped when there is not one:

    docker compose up -d postgres        # about five seconds, no image build
    uv run pytest tests/test_postgres.py

CI starts one as a service. Keep this file quick -- it exists to walk the paths
that differ, not to re-test the engine.
"""

import os
from pathlib import Path

import pytest

from qanat.project import load
from qanat.runner import run_all
from qanat.scaffold import write_project
from qanat.store import Store

DSN = os.environ.get("QANAT_TEST_PG", "postgresql://qanat:qanat@127.0.0.1:5433/qanat")


def _reachable(dsn: str) -> bool:
    try:
        s = Store(dsn)
        s.close()
        return True
    except Exception:  # noqa: BLE001 -- any failure means 'no server here'
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(DSN),
    reason=f"no Postgres at {DSN} — `docker compose up -d postgres` to run these",
)


@pytest.fixture
def pg():
    """A clean server. The store is the schema, so emptying it is the reset."""
    store = Store(DSN)
    store.con.execute('DROP SCHEMA IF EXISTS "qanat_pit" CASCADE')
    for row in store.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall():
        store.con.execute(f'DROP TABLE IF EXISTS "public"."{row[0]}" CASCADE')
    store.close()

    store = Store(DSN)          # rebuilds the meta tables from nothing
    yield store
    store.close()


@pytest.fixture
def project(tmp_path: Path, pg: Store):
    """The scaffolded project, pointed at the server."""
    write_project(tmp_path, "pgtest", store=DSN)
    proj, root = load(tmp_path)
    return pg, proj, root


def test_the_meta_tables_are_created(pg: Store):
    """Bug: `ALTER TABLE ... ADD COLUMN ... DEFAULT false`. DuckDB takes it,
    Postgres refuses a non-constant DEFAULT through the attachment, and the
    store could not open at all."""
    names = {r[0] for r in pg.con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    ).fetchall()}
    assert {"_qanat_runs", "_qanat_backtests", "_qanat_state"} <= names
    assert pg.last_live_backtest() is None


def test_reads_through_a_second_cursor_find_the_meta_tables(pg: Store):
    """Bug: a fresh cursor starts on DuckDB's default schema, not the attached
    one, so every read the console makes returned 500."""
    pg.event("info", "test", "hello")
    assert pg.recent_events(), "recent_events found nothing"
    assert pg.recent_runs() == []
    assert pg.last_run_by_job() == {}, "last_run_by_job is the one that 500'd"
    assert pg.backtests() == []


def test_a_pipeline_pass_lands_a_portfolio(project):
    store, proj, root = project
    results = run_all(store, proj, root)
    assert all(r.ok for r in results), [(r.job_id, r.error) for r in results if not r.ok]
    assert not store.read("weights.target").empty


def test_a_replay_can_rewrite_the_tables_it_shadows(project):
    """Bug: open_pit puts a view over every table, and a step then replaces the
    table underneath it. Postgres will not drop a table a view depends on, so
    every pass failed and every backtest priced at zero."""
    from qanat.backtest import run_backtest

    store, proj, root = project
    assert all(r.ok for r in run_all(store, proj, root))

    prices = store.read(proj.backtest.prices)
    col = proj.backtest.date_column
    frm, to = str(prices[col].min())[:10], str(prices[col].max())[:10]

    res = run_backtest(store, proj, root, frm, to, rebalance="60d", alpha="portfolio")
    assert res.periods, f"no period priced: {res.notes[:3]}"
    assert res.totals["periods"] > 0


def test_a_source_key_upserts_on_the_server(project):
    """`DELETE ... USING` is how the dedupe key replaces a row. It is one more
    statement DuckDB runs happily on a file and has to push down to Postgres."""
    import pandas as pd

    store, _, _ = project
    window = pd.DataFrame({"symbol": ["A", "B"], "ts": ["d1", "d1"], "px": [1.0, 2.0]})
    store.write("raw.px", window, mode="append", key=["symbol", "ts"])
    store.write("raw.px", window, mode="append", key=["symbol", "ts"])
    assert len(store.read("raw.px")) == 2, "the repeated window landed twice"


def test_the_console_can_draw_the_graph(project):
    """Every 500 the container logged came through here."""
    from qanat.api import build_graph

    store, proj, root = project
    run_all(store, proj, root)
    g = build_graph(store, proj, root, None)
    assert g["tables"] and g["jobs"]
    assert any(t["rows"] for t in g["tables"])
