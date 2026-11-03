"""Defining a metric over the API, which is the object the product is named after.

Four things are worth holding here. A formula that cannot run is not stored. A
metric is a **table in the metrics stage**, so the stage is the registry and
nothing keeps a list beside it. Replacing a formula appends its values beside the
old ones rather than over them, so there is no version to bump -- the version is
the definition's digest, and it moves when the formula moves. And the threshold
never appears: that half belongs to whoever set it and travels with the screen
call.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from qanat import metrics
from qanat.project import load, validate
from qanat.store import Store

YAML = """\
project: m
store: {store}
stages:
  - id: raw
    kind: raw
  - id: normalized
    kind: normalized
  - id: metrics
    kind: metrics
ingestion_job_definitions:
  - id: land
    to: [raw.docs]
    connector: seed
    options:
      path: ./seed/docs.csv
transform_job_definitions:
  - id: shape
    from: [raw.docs]
    to:   [normalized.facts]
    script: transforms/shape.sql
corpus:
  documents: raw.docs
  facts: normalized.facts
"""

SHAPE = "SELECT * FROM raw__docs\n"

FACTS = pd.DataFrame({
    "subject": ["AAPL", "MSFT"],
    "value": [10.0, 20.0],
    "as_of": pd.to_datetime(["2024-01-01", "2024-01-01"]),
})


@pytest.fixture
def project(tmp_path: Path):
    """A project on disk with one written table, so a formula can be run."""
    from conftest import STORE_KIND, write_project_text

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    write_project_text(tmp_path, YAML.format(store=store_line))
    (tmp_path / "transforms" / "shape.sql").write_text(SHAPE)
    FACTS.to_csv(tmp_path / "seed" / "docs.csv", index=False)

    pr, root = load(tmp_path)
    store = Store(pr.store_url(root))
    store.write("normalized.facts", FACTS.copy())
    yield pr, store, root
    store.close()


#: A formula carrying its own date, which is the only kind there is.
GOOD = "SELECT subject, as_of AS date, value FROM normalized__facts"


def define(project, **kw):
    pr, store, root = project
    out = metrics.define(store, pr, root, **kw)
    return out, load(root)[0]


# ------------------------------------------------------------- it can be made
def test_a_metric_defined_over_the_api_is_listed_and_screenable(project):
    """The whole point. Nothing wrote one before, and there was no CLI verb."""
    _, store, _ = project
    out, fresh = define(project, mid="val", sql=GOOD, unit="ratio")

    assert out["defined"] == "val"
    assert out["table"] == "metrics.val"
    assert fresh.metric_ids() == ["val"], "the stage is the registry"
    assert [m["id"] for m in metrics.listing(fresh)] == ["val"]

    # And it answers, because define ran it.
    from qanat.screen import metric_values

    got = metric_values(store, fresh, "val", "2024-06-01")
    assert dict(zip(got.subject, got.value)) == {"AAPL": 10.0, "MSFT": 20.0}


def test_defining_it_runs_it_so_it_answers_immediately(project):
    """A metric that exists only as a definition answers nothing, and asking for
    it is the next thing any caller does."""
    out, _ = define(project, mid="val", sql=GOOD)
    assert out["filled"]["status"] == "ok"
    assert out["filled"]["rows"] == 2
    assert out["filled"]["execution"] is not None


def test_a_metric_survives_a_reload_because_it_is_written_to_the_file(project):
    define(project, mid="val", sql=GOOD)
    _, root = load(project[2])
    again, _ = load(root)
    assert again.metric_ids() == ["val"]
    assert again.definition("val") is not None


def test_the_version_is_the_digest_and_not_a_number_to_maintain(project):
    out, fresh = define(project, mid="val", sql=GOOD)
    _, store, _ = project
    assert out["version"], "the digest the execution ran under"
    assert out["version"] == store.definition_version("val")["version"]
    listed = metrics.listing(fresh, store)[0]
    assert listed["version"] == out["version"]


# ------------------------------------------------- a formula that cannot run
def test_a_formula_with_a_syntax_error_is_refused_at_save_time(project):
    with pytest.raises(metrics.MetricError):
        define(project, mid="bad", sql="SELECT FROM WHERE")
    assert load(project[2])[0].metric_ids() == []


def test_a_formula_naming_an_unknown_column_is_refused_with_the_databases_message(project):
    with pytest.raises(metrics.MetricError, match="nosuchcol"):
        define(project, mid="bad",
               sql="SELECT subject, as_of AS date, nosuchcol AS value "
                   "FROM normalized__facts")


def test_a_formula_with_no_date_is_refused_because_there_is_no_series(project):
    """**`date` is what makes a metric a series.** Without it there is only an
    answer to one call, and an answer to one call leaves no row -- so nothing to
    stamp with the execution that made it, and no lineage at all."""
    with pytest.raises(metrics.MetricError, match="missing date"):
        define(project, mid="flat", sql="SELECT subject, value FROM normalized__facts")


def test_a_formula_taking_the_date_as_a_parameter_is_refused(project):
    """It used to be accepted and store nothing. **That kind could not be read
    safely**: with no table a screen had to run the formula, and a formula with
    no date to bind ran over every row -- so a screen at 2024-06-15 could return
    a figure that was not public for five more months."""
    with pytest.raises(metrics.MetricError, match=r":as_of"):
        define(project, mid="param",
               sql="SELECT subject, value FROM normalized__facts WHERE as_of <= :as_of")
    assert load(project[2])[0].metric_ids() == []


def test_an_empty_formula_is_refused(project):
    with pytest.raises(metrics.MetricError, match="formula"):
        define(project, mid="x", sql="   ")


def test_a_formula_reading_no_declared_table_is_refused(project):
    """There would be nothing for a transform to depend on, and nothing to
    recompute it from."""
    with pytest.raises(metrics.MetricError, match="reads no table"):
        define(project, mid="x", sql="SELECT 'A' AS subject, DATE '2024-01-01' AS date, "
                                     "1.0 AS value")


def test_a_table_declared_and_not_yet_written_is_a_warning_not_a_refusal(tmp_path):
    """Defining a metric before the first run is the normal order on a fresh
    project. Refusing it would mean a scaffold could only gain metrics after a
    pipeline run."""
    from conftest import STORE_KIND, write_project_text

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    write_project_text(tmp_path, YAML.format(store=store_line))
    (tmp_path / "transforms" / "shape.sql").write_text(SHAPE)
    FACTS.to_csv(tmp_path / "seed" / "docs.csv", index=False)

    pr, root = load(tmp_path)
    store = Store(pr.store_url(root))
    out = metrics.define(store, pr, root, mid="val", sql=GOOD)
    assert any("not run yet" in w for w in out["warnings"])
    assert out["filled"]["status"] == "not run"
    assert load(root)[0].metric_ids() == ["val"], "stored anyway"
    store.close()


# --------------------------------------------------- replacing, not versioning
def test_replacing_a_formula_appends_its_values_beside_the_old_ones(project):
    """**This is what replaced bumping a version.** The table is keyed and never
    rewritten, so the value somebody was already shown stays readable -- and it
    still names the execution that wrote it, which recorded the digest. An
    integer version alongside was a second version free to disagree, and
    measured, it did."""
    _, store, root = project
    first, _ = define(project, mid="val", sql=GOOD)

    pr2, _ = load(root)
    second = metrics.define(store, pr2, root, mid="val",
                            sql="SELECT subject, as_of AS date, value * 2 AS value "
                                "FROM normalized__facts")
    assert second["replaced"] is True
    assert second["version"] != first["version"], "the digest moved with the formula"

    rows = store.query(f'SELECT subject, value FROM {store.raw_ref("metrics.val")} '
                       "ORDER BY subject, value")
    assert list(rows.value) == [10.0, 20.0, 20.0, 40.0], \
        "both answers are in the table; neither was written over"


def test_a_second_definition_at_one_id_is_not_a_contract_error(project):
    """There is nothing left to collide. A metric used to be an entry in a list,
    so one id could appear twice at one version and the lookup resolved to
    whichever came first."""
    _, _, root = project
    define(project, mid="val", sql=GOOD)
    fresh, froot = load(root)
    res = validate(fresh, froot)
    assert res.ok, res.errors


# ------------------------------------------------------------------- removing
def test_removing_a_metric_is_refused_because_this_server_cannot_check(project):
    define(project, mid="val", sql=GOOD)
    fresh, root = load(project[2])
    with pytest.raises(metrics.MetricError, match="force"):
        metrics.remove(fresh, root, mid="val")


def test_removing_with_force_goes_through_and_leaves_the_values(project):
    """Removing a definition is not a reason to unsay what somebody was shown.
    The rows name the executions that wrote them and stay readable."""
    _, store, root = project
    define(project, mid="val", sql=GOOD)
    fresh, froot = load(root)
    out = metrics.remove(fresh, froot, mid="val", force=True)

    assert out["removed"] == "val"
    assert load(root)[0].metric_ids() == [], "the definition is gone"
    assert store.exists("metrics.val"), "and the values are not"
    assert any("left where they are" in w for w in out["warnings"])


def test_removing_a_metric_that_does_not_exist_names_what_there_is(project):
    define(project, mid="val", sql=GOOD)
    fresh, root = load(project[2])
    with pytest.raises(metrics.MetricError, match="val"):
        metrics.remove(fresh, root, mid="nope", force=True)


# -------------------------------------------------------------- what check says
def test_check_counts_a_metric_table_as_read_because_a_screen_reads_it(project):
    """Without this every metric a project has is reported as read by nothing,
    which is the one table a metric exists to write."""
    define(project, mid="val", sql=GOOD)
    fresh, froot = load(project[2])
    res = validate(fresh, froot)
    assert res.ok, res.errors
    assert not [w for w in res.warnings if "metrics.val" in w and "never read" in w]


def test_check_counts_a_corpus_table_as_read_because_the_reading_tools_read_it(project):
    fresh, froot = load(project[2])
    res = validate(fresh, froot)
    assert not [w for w in res.warnings
                if "normalized.facts" in w and "never read" in w]
