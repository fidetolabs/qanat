"""Every row names the execution that wrote it.

**This is what the one-writer-per-table rule was standing in for.** Its stated
reason was that otherwise nothing can say which pass made the rows in a table --
and measured, that was already the situation: a `raw.documents` row carried
`source: "sec-edgar"` and a JSON blob of the SEC's keys, and no run id anywhere.
Attribution was job-to-table, never row-to-execution.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pandas as pd
import pytest

from qanat.project import load
from qanat.runner import run_all, run_ingestion, run_transform
from qanat.store import EXEC_COL, Store

YAML = """\
project: j
store: {store}
stages:
  - {{id: raw, kind: raw}}
  - {{id: normalized, kind: normalized}}
time_columns:
  raw.docs: as_of
  normalized.facts: as_of
ingestion_job_definitions:
  - id: land
    to: [raw.docs]
    connector: csv
    key: [id]
    options: {{path: ./seed/docs.csv}}
transform_job_definitions:
  - id: shape
    from: [raw.docs]
    to: [normalized.facts]
    script: transforms/t.sql
"""


@pytest.fixture
def ran(tmp_path: Path):
    from conftest import STORE_KIND

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "transforms").mkdir(parents=True)
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "docs.csv").write_text(
        "id,subject,value,as_of\n1,AAPL,10,2024-01-01\n2,MSFT,20,2024-01-02\n")
    (tmp_path / "transforms" / "t.sql").write_text("SELECT * FROM raw__docs")
    (tmp_path / "qanat.yaml").write_text(YAML.format(store=store_line))

    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    results = run_all(store, project, root)
    assert all(r.ok for r in results), [r for r in results if not r.ok]
    try:
        yield project, root, store, {r.definition_id: r for r in results}
    finally:
        store.close()


# ---- A3: a row resolves to an execution, a job, a definition and a version ---
def test_a_row_picked_at_random_names_the_whole_chain(ran):
    _project, _root, store, results = ran

    out = store.provenance("normalized.facts")
    assert out["unknown_rows"] == 0
    assert len(out["executions"]) == 1
    one = out["executions"][0]
    assert one["execution"] == results["shape"].execution_id
    assert one["job"] == results["shape"].job_id
    assert one["definition"] == "shape"
    assert one["definition_version"], "the version the definition was at when it ran"
    assert one["attempt"] == 1
    assert one["status"] == "ok"


def test_one_row_can_be_traced_on_its_own(ran):
    """The question is about a number, not about a table."""
    _project, _root, store, results = ran

    out = store.provenance("raw.docs", where="subject = ?", params=["AAPL"])
    assert [e["rows"] for e in out["executions"]] == [1]
    assert out["executions"][0]["definition"] == "land"
    assert out["executions"][0]["execution"] == results["land"].execution_id


def test_a_transform_and_its_input_name_different_executions(ran):
    """The one that caught a real bug: a `.sql` transform reading a bare table
    name inherited its input's execution column, the engine stamped a second one
    beside it, and the rows then reported whichever the reader found first."""
    _project, _root, store, _results = ran

    docs = store.provenance("raw.docs")["executions"][0]
    facts = store.provenance("normalized.facts")["executions"][0]
    assert docs["definition"] == "land"
    assert facts["definition"] == "shape"
    assert docs["execution"] != facts["execution"]


# ---- the column is the engine's -----------------------------------------------
def test_a_default_read_does_not_carry_the_column(ran):
    """`read_facts` is unchanged by default: this answers "where did this come
    from", and it is not a column every reader carries."""
    _project, _root, store, _ = ran

    assert EXEC_COL not in store.read("raw.docs").columns
    assert EXEC_COL not in store.read("normalized.facts").columns
    assert EXEC_COL not in store.rows("raw.docs").columns


def test_describe_table_does_not_offer_it_either(ran):
    _project, _root, store, _ = ran

    info = store.table_info("raw.docs")
    assert EXEC_COL not in dict(info.columns)


def test_the_column_is_on_the_table_underneath(ran):
    """Hidden from a read, not absent. `lineage` is what reads it."""
    _project, _root, store, _ = ran

    held = {c for (c,) in store.con.execute(
        "SELECT column_name FROM duckdb_columns() WHERE database_name = current_catalog() "
        "AND schema_name = ? AND table_name = 'raw__docs'", [store.home]).fetchall()}
    assert EXEC_COL in held


def test_a_job_cannot_write_the_column(ran):
    """Provenance a script can set is provenance a script can get wrong, and a
    wrong answer about where a number came from is worse than no answer."""
    _project, _root, store, _ = ran

    forged = pd.DataFrame({"id": ["9"], "subject": ["X"], "value": [1],
                           "as_of": pd.to_datetime(["2024-01-03"]),
                           EXEC_COL: [1]})
    with pytest.raises(ValueError, match="written by the engine"):
        store.write("raw.docs", forged, mode="append", key=["id"])


def test_a_transform_returning_the_column_fails_the_transform(tmp_path: Path):
    """The same rule, reached the way a script would reach it."""
    from conftest import STORE_KIND

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "transforms").mkdir(parents=True)
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "docs.csv").write_text("id,subject,value,as_of\n1,A,1,2024-01-01\n")
    (tmp_path / "transforms" / "t.sql").write_text("SELECT * FROM raw__docs")
    (tmp_path / "transforms" / "forge.py").write_text(textwrap.dedent(f'''\
        import pandas as pd


        def run(ctx):
            df = ctx.read("raw.docs")
            df["{EXEC_COL}"] = 1
            return df
        '''))
    (tmp_path / "qanat.yaml").write_text(
        YAML.format(store=store_line).replace("script: transforms/t.sql",
                                              "script: transforms/forge.py"))
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    try:
        assert run_ingestion(store, project, root,
                             project.ingestion_job_definitions[0]).ok
        out = run_transform(store, project, root,
                            project.transform_job_definitions[0])
        assert not out.ok
        assert "written by the engine" in (out.error or "")
    finally:
        store.close()


# ---- the ad-hoc path -----------------------------------------------------------
def test_a_row_landed_by_ingest_filings_names_an_execution(tmp_path: Path, monkeypatch):
    """**It used to write rows and log an event while opening neither a job nor an
    execution**, which is exactly why the documents table held rows nothing could
    claim."""
    from qanat import filings
    from qanat.connectors import sec

    (tmp_path / "data").mkdir(parents=True)
    (tmp_path / "qanat.yaml").write_text(textwrap.dedent("""\
        project: filings
        store: ./data/qanat.duckdb
        stages:
          - {id: raw, kind: raw}
        ingestion_job_definitions:
          - id: filings
            to: [raw.documents]
            connector: sec
            key: [id]
            options:
              companies: [AAPL]
              form: 10-K
              user_agent: qanat tests nobody@example.com
              cache: ./data/documents
        corpus:
          documents: raw.documents
        """))
    project, root = load(tmp_path)
    store = Store(project.store_url(root))

    row = {"accessionNumber": "0000320193-24-000081", "form": "10-K",
           "filingDate": "2024-11-01", "reportDate": "2024-09-28",
           "primaryDocument": "aapl-20240928.htm"}
    body = (tmp_path / "data" / "aapl.htm.gz")
    import gzip

    body.write_bytes(gzip.compress(b"<html/>"))

    monkeypatch.setattr(sec, "resolve_ciks", lambda *a, **k: {"AAPL": "320193"})
    monkeypatch.setattr(sec, "submissions",
                        lambda *a, **k: ({"name": "Apple Inc.", "sic": "3571"}, [row]))
    monkeypatch.setattr(sec, "fetch_body", lambda *a, **k: (body, 7, "abc"))
    monkeypatch.setattr("qanat.xbrl.read_facts",
                        lambda raw: [type("F", (), {"taxonomy": "us-gaap"})()])
    monkeypatch.setattr(filings, "taxonomies", lambda parsed: ["us-gaap", "dei"])
    monkeypatch.setattr(filings, "why_refused", lambda form, found: "")

    try:
        out = filings.ingest(store, project, root,
                             [{"company": "AAPL", "accession": row["accessionNumber"]}])
        assert out["landed"] == 1
        assert out["job"] is not None and out["execution"] is not None

        got = store.provenance("raw.documents")
        assert got["unknown_rows"] == 0, "a hand-triggered pass is still a job"
        one = got["executions"][0]
        assert one["execution"] == out["execution"]
        assert one["definition"] == "filings"
        job = store.job(out["job"])
        assert job["schedule"] is None, "no cadence behind a hand-triggered pass"
    finally:
        store.close()


# ---- rows from before --------------------------------------------------------
def test_a_row_written_before_the_column_existed_reads_as_unknown(ran):
    """Null is reported as unknown rather than silently blank. Nothing is
    backfilled: no honest value exists for a row whose execution was never
    recorded, and a guess there is worse than a null that says so."""
    _project, _root, store, _ = ran

    store.con.execute(
        f'UPDATE "{store.home}"."raw__docs" SET "{EXEC_COL}" = NULL WHERE id = \'1\''
        if store.kind != "parquet" else "SELECT 1")
    if store.kind == "parquet":
        store._publish("raw__docs", (
            f'SELECT * REPLACE (CASE WHEN id = \'1\' THEN NULL ELSE "{EXEC_COL}" END '
            f'AS "{EXEC_COL}") FROM "{store.home}"."raw__docs"'))

    out = store.provenance("raw.docs")
    assert out["unknown_rows"] == 1
    assert "unknown rather than none" in (out["note"] or "")


def test_a_table_with_no_provenance_column_says_so(tmp_path: Path):
    store = Store(str(tmp_path / "bare.duckdb"))
    try:
        store.con.execute('CREATE TABLE "raw__old" AS SELECT 1 AS id')
        out = store.provenance("raw.old")
        assert out["executions"] == []
        assert "before the engine recorded one" in out["note"]
    finally:
        store.close()
