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
    from conftest import STORE_KIND, write_project_text

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "transforms").mkdir(parents=True)
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "docs.csv").write_text(
        "id,subject,value,as_of\n1,AAPL,10,2024-01-01\n2,MSFT,20,2024-01-02\n")
    (tmp_path / "transforms" / "t.sql").write_text("SELECT * FROM raw__docs")
    write_project_text(tmp_path, YAML.format(store=store_line))

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
    from conftest import STORE_KIND, write_project_text

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
    write_project_text(tmp_path, 
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
    from conftest import write_project_text

    from qanat import filings
    from qanat.connectors import sec

    (tmp_path / "data").mkdir(parents=True)
    write_project_text(tmp_path, textwrap.dedent("""\
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


def test_a_row_reports_the_version_that_wrote_it_not_the_current_one(tmp_path):
    """**A3's last clause, which was not met.**

    The chain was row -> execution -> definition_id, and the version was then
    looked up in a table whose primary key is the definition alone -- one row,
    holding whatever is applied *now*. So editing a formula made every older row
    report the new version: a confident answer to the wrong question.

    Measured before the fix on the `check` project: halving a metric's formula
    left the earlier rows claiming the new digest. The digest itself was always
    right and did change on a script edit; nothing recorded which one ran.
    """

    from qanat.project import load
    from qanat.runner import run_definition
    from qanat.store import Store

    root = tmp_path
    (root / "transforms").mkdir()
    (root / "transforms" / "m.sql").write_text(
        "SELECT 'A' AS subject, DATE '2024-01-01' AS date, 1.0 AS value")
    (root / "qanat.yaml").write_text(
        "project: p\nstore: ./data\nstages:\n- id: metrics\n  kind: features\n")
    (root / "jobs.yaml").write_text(
        "transform_job_definitions:\n"
        "- id: m\n  from: []\n  to: [metrics.m]\n  script: transforms/m.sql\n"
        "  key: [subject, date]\n")

    project, r = load(str(root))
    store = Store(project.store_url(r))
    run_definition(store, project, r, "m")
    first = store.provenance("metrics.m")["executions"][0]
    wrote_it = first["definition_version"]
    assert wrote_it and "not recorded" not in wrote_it

    # Edit the formula. The values change, so new rows land beside the old ones.
    (root / "transforms" / "m.sql").write_text(
        "SELECT 'A' AS subject, DATE '2024-01-01' AS date, 2.0 AS value")
    project, r = load(str(root))
    run_definition(store, project, r, "m")

    now = store.definition_version("m")["version"]
    assert now != wrote_it, "editing the script has to move the digest"

    by_execution = {e["execution"]: e["definition_version"]
                    for e in store.provenance("metrics.m")["executions"]}
    assert by_execution[first["execution"]] == wrote_it, \
        "the first attempt still reports the digest it ran"
    assert now in by_execution.values(), "and the second reports the new one"
    assert len(set(by_execution.values())) == 2, \
        "two versions in one table, each row naming its own"
    store.close()


def test_an_execution_from_before_the_column_says_so_rather_than_guessing(tmp_path):
    """A null is honest; the current digest wearing the old one's place is not."""
    from qanat.project import load
    from qanat.runner import run_definition
    from qanat.store import Store

    root = tmp_path
    (root / "transforms").mkdir()
    (root / "transforms" / "m.sql").write_text(
        "SELECT 'A' AS subject, DATE '2024-01-01' AS date, 1.0 AS value")
    (root / "qanat.yaml").write_text(
        "project: p\nstore: ./data\nstages:\n- id: metrics\n  kind: features\n")
    (root / "jobs.yaml").write_text(
        "transform_job_definitions:\n"
        "- id: m\n  from: []\n  to: [metrics.m]\n  script: transforms/m.sql\n")

    project, r = load(str(root))
    store = Store(project.store_url(r))
    run_definition(store, project, r, "m")

    # Blank what the attempt recorded, which is the state of a store written
    # before this column existed.
    from qanat.store import EXECUTIONS

    store.con.execute(
        f"UPDATE {store._meta_home}{EXECUTIONS} SET definition_version = NULL")
    reported = store.provenance("metrics.m")["executions"][0]["definition_version"]
    assert reported.endswith("(current, not recorded)"), \
        "an unrecorded version is labelled, not passed off as the one that ran"
    store.close()


def test_the_lookahead_guard_asks_what_this_execution_wrote(tmp_path):
    """**It used to ask the table, which a keyed table makes the wrong question.**

    A keyed table is never rewritten, so one filled by an ordinary run keeps
    those rows forever -- and asking `max_time` of the table then accused every
    later as-of run of having written them. Measured on the leak battery: a
    metric table filled normally failed every screened run after it.

    What makes the right question answerable is T-04's column. The rows name the
    execution that wrote them, so the guard can ask about this pass only.
    """
    import pandas as pd

    from qanat.project import load
    from qanat.runner import run_definition
    from qanat.store import Store

    root = tmp_path
    (root / "transforms").mkdir()
    (root / "transforms" / "px.sql").write_text(
        "SELECT symbol AS subject, CAST(date AS DATE) AS date, close AS value "
        "FROM raw__bars")
    (root / "qanat.yaml").write_text(
        "project: p\nstore: ./data/q.duckdb\n"
        "stages:\n- {id: raw, kind: raw}\n- {id: metrics, kind: metrics}\n"
        "time_columns:\n  raw.bars: date\n  metrics.px: date\n")
    (root / "jobs.yaml").write_text(
        "transform_job_definitions:\n"
        "- id: px\n  from: [raw.bars]\n  to: [metrics.px]\n"
        "  script: transforms/px.sql\n  key: [subject, date]\n")
    (root / "data").mkdir()

    project, r = load(str(root))
    store = Store(project.store_url(r))
    store.write("raw.bars", pd.DataFrame({
        "symbol": ["A"] * 4,
        "date": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01", "2024-07-18"]),
        "close": [1.0, 2.0, 3.0, 4.0]}), mode="replace", key=["symbol", "date"])

    # An ordinary run fills the table to the end of the bars.
    assert run_definition(store, project, r, "px").ok
    assert str(store.max_time("metrics.px"))[:10] == "2024-07-18"

    # Then a screened run at an earlier date. The as-of views keep the later
    # bars out of what it reads, so it writes nothing new -- and it must not be
    # blamed for the rows the first run left.
    store.open_pit("2024-03-01", project.time_columns)
    try:
        out = run_definition(store, project, r, "px", as_of="2024-03-01")
    finally:
        store.close_pit()
    assert out.ok, out.error
    assert str(store.max_time("metrics.px"))[:10] == "2024-07-18", \
        "the earlier rows are still there, which is the point of never replacing"
    store.close()
