"""The four objects, and the words they are named in.

A job is a definition, a schedule over it, a job for one exact datetime, and an
execution of that job. These are the tests that hold the split apart -- a
definition that learns a cadence, or a schedule that learns a selection, puts
the two concerns back in one object and the asymmetry comes back with them.
"""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from conftest import write_project_text

from qanat.models import IngestionJobDefinition, JobSchedule, TransformJobDefinition
from qanat.project import ProjectError, load, validate
from qanat.store import Store


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transforms" / "t.sql").write_text("SELECT * FROM raw__docs")
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed" / "docs.csv").write_text("id,as_of\n1,2024-01-01\n")
    write_project_text(tmp_path, body)
    return tmp_path


BASE = """\
project: j
store: ./data
stages:
  - {id: raw, kind: raw}
  - {id: normalized, kind: normalized}
ingestion_job_definitions:
  - id: land
    to: [raw.docs]
    connector: csv
    key: [id]
    options: {path: ./seed/docs.csv}
transform_job_definitions:
  - id: shape
    from: [raw.docs]
    to: [normalized.facts]
    script: transforms/t.sql
"""


# ---- A1: neither object holds the other's fields -----------------------------
def test_a_definition_carrying_a_cadence_is_refused_at_load(tmp_path: Path):
    """Refused, not ignored. An old config that silently lost its schedule would
    keep running and quietly stop polling."""
    root = _project(tmp_path, BASE + "    schedule: \"0 6 * * *\"\n")
    with pytest.raises(ProjectError) as exc:
        load(root)
    assert "names no cadence" in str(exc.value)
    assert "job_schedules" in str(exc.value), "the refusal has to name where it goes"


def test_a_schedule_carrying_a_selection_is_refused(tmp_path: Path):
    """The other direction, and it matters as much: a schedule that could narrow
    the selection would put both concerns back in one object from its side."""
    root = _project(tmp_path, BASE + """\
job_schedules:
  - id: land_daily
    definition: land
    cron: "0 6 * * *"
    companies: [AAPL]
""")
    with pytest.raises(ProjectError) as exc:
        load(root)
    assert "names no selection" in str(exc.value)


def test_a_definition_names_no_cadence_field_at_all():
    assert "schedule" not in IngestionJobDefinition.model_fields
    assert "schedule" not in TransformJobDefinition.model_fields
    assert "cron" not in IngestionJobDefinition.model_fields


def test_a_schedule_names_no_selection_field_at_all():
    for field in ("companies", "options", "connector", "script", "writes", "reads"):
        assert field not in JobSchedule.model_fields, field


def test_a_definition_with_no_schedule_stays_ordinary_and_runnable(tmp_path: Path):
    """How the `check` project works, and it has to keep working."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    assert validate(project, where).ok
    assert project.job_schedules == []

    from qanat.runner import run_all

    store = Store(project.store_url(where))
    try:
        assert all(r.ok for r in run_all(store, project, where))
    finally:
        store.close()


# ---- A2 (half of it): one definition, several cadences -----------------------
def test_one_definition_may_carry_two_cadences(tmp_path: Path):
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: land_daily,  definition: land, cron: "0 6 * * *"}
  - {id: land_weekly, definition: land, cron: "0 6 * * 1"}
""")
    project, where = load(root)
    assert validate(project, where).ok
    assert [s.cron for s in project.schedules_for("land")] == ["0 6 * * *", "0 6 * * 1"]


def test_a_schedule_pointing_at_nothing_is_refused_by_check(tmp_path: Path):
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: ghost, definition: nobody, cron: "0 6 * * *"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert any("nobody" in e for e in rep.errors), rep.errors


def test_a_schedule_id_may_not_collide_with_a_definition_id(tmp_path: Path):
    """`plan` tracks drift on both and keys that memory on the id, so a collision
    would have one of them reporting the other's history."""
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: land, definition: land, cron: "0 6 * * *"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert any("duplicate id" in e for e in rep.errors), rep.errors


# ---- A8: a retried job is one job and several executions ---------------------
def test_a_definition_that_fails_twice_then_succeeds_is_one_job(tmp_path: Path):
    """**The reason the job layer exists is retries, not backfills.** With one row
    per attempt and nothing grouping them, "did the 07:00 poll succeed?" had no
    object to ask."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        job = store.open_job("land", "ingestion", "2026-11-01 07:00:00", "land_daily")
        for attempt in (1, 2):
            run = store.start_execution(job, "land", "ingestion", ["raw.docs"])
            store.end_execution(run, "failed", 0, f"attempt {attempt} broke")
        # The same instant again: the same job, a third attempt.
        again = store.open_job("land", "ingestion", "2026-11-01 07:00:00", "land_daily")
        assert again == job, "a re-run opened a second job instead of a third attempt"
        run = store.start_execution(again, "land", "ingestion", ["raw.docs"])
        store.end_execution(run, "ok", 7)

        seen = store.job(job)
        assert len(seen["attempts"]) == 3
        assert [a["attempt"] for a in seen["attempts"]] == [1, 2, 3]
        assert seen["status"] == "ok", "one attempt succeeded, so the job succeeded"
        assert seen["schedule"] == "land_daily"
        assert seen["scheduled_for"].startswith("2026-11-01 07:00")
    finally:
        store.close()


def test_an_ad_hoc_pass_is_a_job_with_no_schedule(tmp_path: Path):
    """Not the absence of a job. That is what makes its rows attributable."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        from qanat.runner import run_ingestion

        result = run_ingestion(store, project, where,
                               project.ingestion_job_definitions[0])
        assert result.ok
        assert result.job_id is not None
        job = store.job(result.job_id)
        assert job["schedule"] is None, "an ad-hoc pass has no cadence behind it"
        assert job["definition"] == "land"
        assert len(job["attempts"]) == 1
    finally:
        store.close()


def test_an_execution_resolves_to_its_job_and_its_definition(tmp_path: Path):
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        from qanat.runner import run_ingestion

        result = run_ingestion(store, project, where,
                               project.ingestion_job_definitions[0])
        found = store.execution(result.execution_id)
        assert found["job_id"] == result.job_id
        assert found["definition"] == "land"
        assert found["attempt"] == 1
        assert found["targets"] == ["raw.docs"]
        version = store.definition_version("land")
        assert version and version["version"], "the definition's version resolves too"
    finally:
        store.close()


# ---- A5: the vocabulary ------------------------------------------------------
#: Where the retired words are still allowed to appear. The changelog is history
#: and the migration is the one thing that reads the old spellings.
_VOCABULARY_EXEMPT = ("migrate.py",)


def _documents_the_column(hit: str) -> bool:
    """Is this the connector's own note about the provenance column it writes?

    Two shapes: a row in the module's column table (`    source   'yfinance'`),
    and the value it lands (`` `source: yfinance` ``).
    """
    body = hit.split(":", 2)[-1]
    return bool(re.match(r"\s*source\s{2,}\S", body)) or "`source: " in body


def _grep(pattern: str, *paths: str) -> list[str]:
    out = subprocess.run(
        ["grep", "-rnE", pattern, *paths],
        capture_output=True, text=True, check=False, cwd=Path(__file__).parent.parent,
    )
    return [
        line for line in out.stdout.splitlines()
        if line and not any(x in line.split(":")[0] for x in _VOCABULARY_EXEMPT)
        and "__pycache__" not in line
        # A rename has to be able to name what it renamed. The retired word in
        # backticks is the sentence explaining that it is retired.
        and "`step`" not in line and "`steps:`" not in line
        and "`source`" not in line and "`sources:`" not in line
        and "`qanat plan`" not in line
        # "open source" is the licence, not the config declaration
        and "open-source" not in line and "open source" not in line
    ]


def test_the_word_step_is_gone_from_the_engine():
    """`step` named a position in a sequence and said nothing about a table going
    in and a table coming out, which is the only thing one does."""
    hits = _grep(r"\b(step|steps)\b", "src/qanat", "docs")
    # These tool names still carry the word. Renaming them is a separate
    # decision, to be made from a flow rather than from this test, so they are
    # named here rather than silently passed over.
    hits = [h for h in hits if not any(
        name in h for name in ("list_pipeline", "define_transform", "read_script", "remove_transform"))]
    assert not hits, "\n".join(hits)


def test_the_word_source_survives_only_as_row_provenance():
    """It meant four things: a config declaration, a module in here, the
    `connector:` value, and a string on every row. Only the last was ever
    unambiguous, and it is the only one that keeps the word."""
    hits = [
        h for h in _grep(r"\bsources?\b", "src/qanat", "docs")
        # the provenance column, named as a key or described in prose about it
        if '"source"' not in h and "'source'" not in h
        and "source_keys" not in h and "source_id" not in h
        and "sec-edgar" not in h
        # A connector documents the column it writes, which A5 allows by name:
        # a row in its column table, or the value it puts there.
        and not _documents_the_column(h)
        # the paragraph in each module that explains why the word went
        and "qanat/sources" not in h and "was `qanat/sources/`" not in h
        # as above: renaming `save_source` is a separate decision, from a flow
        and "define_ingestion" not in h
    ]
    assert not hits, "\n".join(hits)


def test_the_connector_package_is_named_for_what_it_is():
    import qanat.connectors

    assert set(qanat.connectors.ADAPTERS) == {"rest", "sql", "csv", "sec", "yfinance"}
    with pytest.raises(ImportError):
        __import__("qanat.sources")


def test_the_old_spellings_are_refused_rather_than_aliased(tmp_path: Path):
    """An alias leaves both vocabularies in the record, which is the state this
    change exists to end."""
    # Written whole on purpose: this is the file a migration reads.
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transforms" / "t.sql").write_text("SELECT 1")
    (tmp_path / "qanat.yaml").write_text(
        BASE.replace("ingestion_job_definitions:", "sources:"))
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "old vocabulary" in str(exc.value)
    assert "qanat migrate" in str(exc.value), "the refusal names what to run"


# ---- the migration -----------------------------------------------------------
def test_the_migration_moves_the_cadence_into_its_own_row(tmp_path: Path):
    from qanat.migrate import apply_migration, plan_migration

    (tmp_path / "steps").mkdir(parents=True)
    (tmp_path / "steps" / "t.sql").write_text("SELECT * FROM raw__docs")
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "docs.csv").write_text("id,as_of\n1,2024-01-01\n")
    (tmp_path / "qanat.yaml").write_text(textwrap.dedent("""\
        project: j
        store: ./data
        # a note somebody wrote and means to keep
        stages:
          - {id: raw, kind: raw}
          - {id: normalized, kind: normalized}
        sources:
          - id: land
            to: [raw.docs]
            connector: csv
            mode: replace
            key: [id]
            schedule: "0 6 * * *"
            options: {path: ./seed/docs.csv}
        steps:
          - id: shape
            from: [raw.docs]
            to: [normalized.facts]
            script: steps/t.sql
        """))

    m = plan_migration(tmp_path)
    assert m.needed
    apply_migration(m)

    project, _ = load(tmp_path)
    assert [d.id for d in project.ingestion_job_definitions] == ["land"]
    assert [d.id for d in project.transform_job_definitions] == ["shape"]
    assert [(s.definition, s.cron) for s in project.job_schedules] == [("land", "0 6 * * *")]
    assert project.ingestion_job_definitions[0].mode == "append", \
        "replace deletes every row outside the current selection"
    assert project.transform_job_definitions[0].script == "transforms/t.sql"
    assert (tmp_path / "transforms" / "t.sql").is_file()
    assert "a note somebody wrote" in (tmp_path / "qanat.yaml").read_text()
    assert (tmp_path / "qanat.yaml.before-the-job-model").is_file(), \
        "a comment above a key that no longer exists survives only in the copy"


def test_migrating_a_project_that_is_already_new_does_nothing(tmp_path: Path):
    from qanat.migrate import plan_migration

    root = _project(tmp_path, BASE)
    assert not plan_migration(root).needed


def test_migrate_prints_before_it_writes(tmp_path: Path):
    """The one command that edits a file somebody wrote by hand."""
    (tmp_path / "qanat.yaml").write_text(textwrap.dedent("""\
        project: j
        store: ./data
        stages:
          - {id: raw, kind: raw}
        sources:
          - {id: land, to: [raw.docs], connector: csv, key: [id], options: {path: ./x.csv}}
        """))
    before = (tmp_path / "qanat.yaml").read_text()
    out = subprocess.run([sys.executable, "-m", "qanat", "migrate", str(tmp_path)],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stderr
    assert "nothing was written" in out.stdout
    assert (tmp_path / "qanat.yaml").read_text() == before


# ---- upgrading a store written by 0.10.x -------------------------------------
@pytest.mark.parametrize("kind", ["parquet", "duckdb"])
def test_a_store_from_before_the_rename_keeps_its_history(tmp_path: Path, kind: str):
    """**Nothing covered this, and it was broken on the store kind the product
    actually uses.**

    The rename-on-open was guarded by `_exists`, which answers for a *data*
    table -- and on a parquet store that means asking whether any parquet files
    are on disk. The metadata tables are never parquet, they live in the
    attached `meta` database, so `_exists` said no for every one of them: the
    rename never fired, `CREATE TABLE IF NOT EXISTS` made the new tables empty
    beside the old ones, and a project upgrading lost its whole execution
    history with nothing saying so. Every test passed, because every test builds
    a fresh store.
    """
    import duckdb

    if kind == "parquet":
        root = tmp_path / "corpus"
        (root / "tables").mkdir(parents=True)
        meta, target = root / "meta.duckdb", str(root)
    else:
        meta = target = tmp_path / "q.duckdb"
        target = str(target)

    con = duckdb.connect(str(meta))
    con.execute("""CREATE TABLE _qanat_runs (run_id BIGINT PRIMARY KEY, job_id VARCHAR,
        kind VARCHAR, started_at TIMESTAMP, ended_at TIMESTAMP, status VARCHAR,
        rows_out BIGINT, targets VARCHAR, error VARCHAR)""")
    con.execute("INSERT INTO _qanat_runs VALUES "
                "(1, 'land', 'source', now(), now(), 'ok', 42, 'raw.a', NULL)")
    con.execute("""CREATE TABLE _qanat_state (job_id VARCHAR PRIMARY KEY,
        digest VARCHAR, spec VARCHAR, applied_at TIMESTAMP)""")
    con.execute("INSERT INTO _qanat_state VALUES ('land', 'abc123', '{}', now())")
    con.execute("""CREATE TABLE _qanat_events (ts TIMESTAMP, level VARCHAR,
        job_id VARCHAR, message VARCHAR)""")
    con.close()

    store = Store(target)
    try:
        store._meta()
        held = {r[0] for r in store.con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE table_name LIKE '_qanat%'"
        ).fetchall()}
        assert "_qanat_runs" not in held, "renamed, not left beside the new one"
        assert "_qanat_state" not in held
        assert {"_qanat_job_execution", "_qanat_job_definition_version"} <= held

        history = store.recent_executions()
        assert len(history) == 1, "the execution survived the rename"
        one = history[0]
        # **The old `job_id` held the definition**, under a name the job layer
        # now uses for one definition at one instant.
        assert one["definition_id"] == "land"
        assert one["status"] == "ok" and one["rows_out"] == 42
        assert one["job_id"] is None, "no job existed above it, and none is invented"

        assert store.load_state() == {"land": {"digest": "abc123", "spec": "{}"}}
    finally:
        store.close()


# ---- the metrics migration ----------------------------------------------------
OLD_WITH_METRICS = """\
project: legacy
store: {store}
stages:
  - {{id: raw, kind: raw}}
  - {{id: features, kind: features}}
time_columns:
  raw.facts: as_of
corpus:
  facts: raw.facts
  metrics: features.metrics
sources:
  - id: land
    to: [raw.facts]
    connector: csv
    options: {{path: ./seed/facts.csv}}
"""

OLD_METRICS_YAML = """\
metrics:
  - id: eps_series
    version: 1
    unit: USD
    sql: SELECT subject, as_of AS date, eps AS value FROM raw__facts
  - id: eps_series
    version: 2
    unit: USD
    sql: SELECT subject, as_of AS date, eps * 1.0 AS value FROM raw__facts
  - id: eps_max
    version: 1
    sql: SELECT subject, max(eps) AS value FROM raw__facts WHERE as_of <= :as_of GROUP BY 1
"""


def _old_project(tmp_path):
    from conftest import STORE_KIND

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "qanat.yaml").write_text(OLD_WITH_METRICS.format(store=store_line))
    (tmp_path / "metrics.yaml").write_text(OLD_METRICS_YAML)
    (tmp_path / "seed" / "facts.csv").write_text(
        "subject,eps,as_of\nAAPL,6.0,2024-01-01\nMSFT,10.0,2024-01-01\n")
    return tmp_path


def test_a_metric_list_migrates_into_transforms_writing_their_own_tables(tmp_path):
    """**The list is read out of `metrics.yaml`, which this used to never open.**
    A project already split holds its metrics there, so reading only the
    architecture file migrated nothing -- the one file with the metrics in it."""
    from qanat.migrate import apply_migration, plan_migration
    from qanat.project import load, validate
    from qanat.runner import run_all
    from qanat.store import Store

    root = _old_project(tmp_path)
    m = plan_migration(root)
    assert m.needed
    apply_migration(m)

    project, r = load(root)
    assert project.metrics_stage() == "metrics", \
        "a stage declaring kind: metrics, which is the registry"
    assert project.metric_ids() == ["eps_series"]
    assert (r / "transforms" / "metrics" / "eps_series.sql").is_file()

    definition = project.definition("eps_series")
    assert definition.writes == ["metrics.eps_series"]
    assert definition.reads == ["raw.facts"], "read off the formula"
    assert definition.key == ["subject", "date"], "a metric value is never replaced"
    assert definition.options["unit"] == "USD", "unit moved onto the definition"
    assert validate(project, r).ok, validate(project, r).errors

    store = Store(project.store_url(r))
    try:
        assert all(x.ok for x in run_all(store, project, r))
        from qanat.screen import metric_values

        got = metric_values(store, project, "eps_series", "2024-06-01")
        assert dict(zip(got.subject, got.value)) == {"AAPL": 6.0, "MSFT": 10.0}
    finally:
        store.close()


def test_the_metrics_stage_is_not_the_stage_the_old_corpus_ref_named(tmp_path):
    """`features.metrics` was a *table* inside a features stage. Reusing the
    stage part of it landed every metric in `features`, where nothing recognised
    it -- measured, the migrated project loaded, checked, ran, and reported no
    metrics at all."""
    from qanat.migrate import apply_migration, plan_migration
    from qanat.project import load

    root = _old_project(tmp_path)
    apply_migration(plan_migration(root))
    project, _ = load(root)
    assert [s.id for s in project.stages] == ["raw", "features", "metrics"]
    assert project.definition("eps_series").writes == ["metrics.eps_series"]


def test_a_formula_taking_the_date_as_a_parameter_is_reported_not_guessed(tmp_path):
    """Deciding which column carries the date each value became true is a
    judgement about what the formula means. Guessing it would store numbers under
    dates they do not mean, so it is named and left in the backup."""
    from qanat.migrate import apply_migration, plan_migration
    from qanat.project import load

    root = _old_project(tmp_path)
    m = plan_migration(root)
    assert any("eps_max" in n and "NOT migrated" in n for n in m.notes), m.notes
    apply_migration(m)

    project, _ = load(root)
    assert "eps_max" not in project.metric_ids()
    kept = (root / "metrics.yaml.before-the-job-model").read_text()
    assert "eps_max" in kept, "the formula survives where it can be read"
    assert not (root / "metrics.yaml").exists()


def test_two_versions_of_one_formula_become_one_definition_and_say_so(tmp_path):
    """The integer version is gone -- a metric's version is its definition's
    digest -- so two entries at one id would be two definitions writing one table
    under one name."""
    from qanat.migrate import plan_migration

    root = _old_project(tmp_path)
    m = plan_migration(root)
    assert any("versions 1, 2" in n and "from version 2" in n for n in m.notes), m.notes


def test_corpus_metrics_is_dropped_because_one_ref_cannot_name_them(tmp_path):
    from qanat.migrate import apply_migration, plan_migration
    from qanat.project import load

    root = _old_project(tmp_path)
    m = plan_migration(root)
    assert any("corpus.metrics" in n for n in m.notes)
    apply_migration(m)
    project, _ = load(root)
    assert project.corpus is not None
    assert not hasattr(project.corpus, "metrics")
