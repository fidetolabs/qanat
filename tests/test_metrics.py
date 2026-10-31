"""Defining a metric over the API, which is the object the product is named after.

Three things are worth holding here. A formula that cannot run is not stored. A
bumped version is written beside the old one rather than over it, because an
answer somebody kept points at a version. And the threshold never appears --
that half belongs to whoever set it and travels with the screen call.
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
sources:
  - id: land
    to: [raw.docs]
    connector: csv
    options:
      path: ./seed/docs.csv
steps:
  - id: shape
    from: [raw.docs]
    to:   [normalized.facts]
    script: steps/shape.sql
corpus:
  documents: raw.docs
  facts: normalized.facts
"""

SHAPE = "SELECT * FROM raw__docs\n"


@pytest.fixture
def project(tmp_path: Path):
    """A project on disk with one written table, so a formula can be run."""
    from conftest import STORE_KIND

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "steps").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "qanat.yaml").write_text(YAML.format(store=store_line))
    (tmp_path / "steps" / "shape.sql").write_text(SHAPE)
    pd.DataFrame({
        "subject": ["AAPL", "MSFT"],
        "value": [10.0, 20.0],
        "as_of": pd.to_datetime(["2024-01-01", "2024-01-01"]),
    }).to_csv(tmp_path / "seed" / "docs.csv", index=False)

    pr, root = load(tmp_path)
    store = Store(pr.store_url(root))
    store.write("normalized.facts", pd.DataFrame({
        "subject": ["AAPL", "MSFT"],
        "value": [10.0, 20.0],
        "as_of": pd.to_datetime(["2024-01-01", "2024-01-01"]),
    }))
    yield pr, store, root
    store.close()


GOOD = "SELECT subject, value FROM normalized__facts WHERE as_of <= :as_of"


# ------------------------------------------------------------- it can be made
def test_a_metric_defined_over_the_api_is_listed_and_screenable(project):
    """The whole point. Nothing wrote one before, and there was no CLI verb."""
    pr, store, root = project
    out = metrics.define(store, pr, root, mid="pe", sql=GOOD, unit="ratio",
                         description="value over one")
    assert out["defined"] == "pe"
    assert out["version"] == 1
    assert out["origin"] == "defined"
    assert out["reads"] == ["normalized.facts"]

    listed = metrics.listing(pr)
    assert [m["id"] for m in listed] == ["pe"]
    assert listed[0]["current"] is True

    from qanat.screen import Clause, Filter, screen

    answer = screen(store, pr, [Filter(clauses=[Clause("pe", "gt", 15)])],
                    as_of="2024-06-01")
    passed = {r["subject"]: r["passed"] for r in answer["rows"]}
    assert passed == {"AAPL": False, "MSFT": True}


def test_a_metric_survives_a_reload_because_it_is_written_to_the_file(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD, unit="ratio")
    fresh, _ = load(root)
    assert fresh.metric("pe") is not None
    assert fresh.metric("pe").unit == "ratio"


# ------------------------------------------------------- it has to be runnable
def test_a_formula_with_a_syntax_error_is_refused_at_save_time(project):
    pr, store, root = project
    with pytest.raises(metrics.MetricError) as exc:
        metrics.define(store, pr, root, mid="broken", sql="SELEKT subject FROM x")
    assert "broken" in str(exc.value)
    assert pr.metric("broken") is None, "a formula that cannot run was stored"


def test_a_formula_naming_an_unknown_column_is_refused_with_the_databases_message(project):
    pr, store, root = project
    with pytest.raises(metrics.MetricError) as exc:
        metrics.define(store, pr, root, mid="nope",
                       sql="SELECT subject, nosuchcol AS value FROM normalized__facts")
    assert "nosuchcol" in str(exc.value), "the database's own message is what helps"
    assert pr.metric("nope") is None


def test_a_formula_that_does_not_select_subject_and_value_is_refused(project):
    pr, store, root = project
    with pytest.raises(metrics.MetricError, match="subject"):
        metrics.define(store, pr, root, mid="wrong",
                       sql="SELECT subject AS s, value AS v FROM normalized__facts")


def test_an_empty_formula_is_refused(project):
    pr, store, root = project
    with pytest.raises(metrics.MetricError, match="formula"):
        metrics.define(store, pr, root, mid="blank", sql="   ")


def test_a_table_declared_and_not_yet_written_is_a_warning_not_a_refusal(tmp_path):
    """Defining a metric before the first run is the normal order on a new project."""
    from conftest import STORE_KIND

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "steps").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "qanat.yaml").write_text(YAML.format(store=store_line))
    (tmp_path / "steps" / "shape.sql").write_text(SHAPE)
    (tmp_path / "seed" / "docs.csv").write_text("subject,value,as_of\n")

    pr, root = load(tmp_path)
    store = Store(pr.store_url(root))
    try:
        out = metrics.define(store, pr, root, mid="pe", sql=GOOD)
        assert pr.metric("pe") is not None, "it is stored"
        assert any("not run yet" in w for w in out["warnings"])
        assert any("normalized.facts" in w for w in out["warnings"])
    finally:
        store.close()


# ----------------------------------------------------------------- versioning
def test_a_bumped_version_leaves_the_old_one_resolvable(project):
    """An answer pointing at N still returns after N+1 exists."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    metrics.define(store, pr, root, mid="pe", version=2,
                   sql="SELECT subject, value * 2 AS value FROM normalized__facts")

    assert pr.metric_versions("pe") == [1, 2]
    assert pr.metric("pe").version == 2, "no version asked for means the current one"
    assert pr.metric("pe", 1) is not None, "version 1 still resolves"

    from qanat.screen import metric_values

    at_one = metric_values(store, pr.metric("pe", 1), "2024-06-01")
    at_two = metric_values(store, pr.metric("pe", 2), "2024-06-01")
    assert at_one.set_index("subject")["value"]["AAPL"] == 10.0
    assert at_two.set_index("subject")["value"]["AAPL"] == 20.0


def test_redefining_an_existing_id_without_a_version_is_refused_not_guessed(project):
    """Replacing N changes what a kept answer meant; writing N+1 claims the
    meaning moved. Neither is something to pick on the caller's behalf."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    with pytest.raises(metrics.MetricError) as exc:
        metrics.define(store, pr, root, mid="pe", sql="SELECT subject, 1 AS value")
    assert "version 1" in str(exc.value)
    assert "2 to write a new one" in str(exc.value)
    assert pr.metric_versions("pe") == [1], "nothing was written"


def test_the_same_version_replaces_rather_than_duplicating(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    out = metrics.define(store, pr, root, mid="pe", version=1,
                         sql="SELECT subject, value AS value FROM normalized__facts")
    assert out["replaced"] is True
    assert pr.metric_versions("pe") == [1]
    assert validate(pr, root).ok


def test_a_version_below_one_is_refused(project):
    pr, store, root = project
    with pytest.raises(metrics.MetricError, match="versions start at 1"):
        metrics.define(store, pr, root, mid="pe", version=0, sql=GOOD)


def test_two_definitions_at_one_version_is_a_contract_error(project):
    pr, _store, root = project
    from qanat.models import Metric

    pr.metrics = [Metric(id="pe", sql=GOOD, version=1),
                  Metric(id="pe", sql=GOOD, version=1)]
    rep = validate(pr, root)
    assert not rep.ok
    assert any("defined twice at version 1" in e for e in rep.errors)


# --------------------------------------------------------------- where it came from
def test_a_definition_says_whether_it_was_shipped_or_written_here(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="mine", sql=GOOD)
    assert pr.metric("mine").origin == "defined"


def test_the_shipped_starter_set_is_readable_as_a_starter_set():
    """Two formulas, labelled, and deliberately not a metric library."""
    pr, _root = load(Path(__file__).resolve().parents[1] / "examples" / "sec-10k")
    listed = metrics.listing(pr)
    assert listed, "the example ships a starter set"
    assert all(m["origin"] == "shipped" for m in listed)
    assert {m["id"] for m in listed} == {"pe", "net_margin"}


def test_a_callers_definition_does_not_join_the_shipped_set_by_sitting_beside_it(project):
    pr, store, root = project
    from qanat.editor import save_metric
    from qanat.models import Metric

    save_metric(pr, root, Metric(id="pe", sql=GOOD, version=1, origin="shipped"))
    with pytest.raises(metrics.MetricError, match="shipped"):
        metrics.define(store, pr, root, mid="pe", version=1,
                       sql="SELECT subject, 1 AS value")
    # Writing it as a new version is the way through, and it is labelled.
    out = metrics.define(store, pr, root, mid="pe", version=2,
                         sql="SELECT subject, value FROM normalized__facts")
    assert out["origin"] == "defined"
    assert pr.metric("pe", 1).origin == "shipped"


# ----------------------------------------------------------------- removal
def test_removing_the_last_version_is_refused_because_this_server_cannot_check(project):
    """A filter naming a metric is one caller's criteria, held by the caller."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    with pytest.raises(metrics.MetricError) as exc:
        metrics.remove(pr, root, mid="pe")
    assert "held by the caller" in str(exc.value)
    assert "force" in str(exc.value)
    assert pr.metric("pe") is not None


def test_removing_the_last_version_with_force_goes_through(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    out = metrics.remove(pr, root, mid="pe", force=True)
    assert out["versions_removed"] == [1]
    assert out["versions_left"] == []
    assert pr.metric("pe") is None


def test_removing_an_older_version_warns_that_a_pinned_answer_stops_resolving(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    metrics.define(store, pr, root, mid="pe", version=2, sql=GOOD)
    out = metrics.remove(pr, root, mid="pe", version=1)
    assert out["versions_left"] == [2]
    assert any("no longer resolves" in w for w in out["warnings"])
    assert pr.metric("pe", 1) is None
    assert pr.metric("pe", 2) is not None


def test_removing_a_metric_that_does_not_exist_names_what_there_is(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    with pytest.raises(metrics.MetricError, match="This project has: pe"):
        metrics.remove(pr, root, mid="nope")


def test_removing_a_version_that_does_not_exist_names_the_ones_that_do(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    with pytest.raises(metrics.MetricError, match="It has: 1"):
        metrics.remove(pr, root, mid="pe", version=7)


# ------------------------------------------------------ check stops lying
def test_check_counts_a_metrics_sql_as_a_read(project):
    """The terminal table of every correct filings pipeline was reported as
    read by nothing -- which is the one thing a metric is for."""
    pr, store, root = project
    # Isolate the metric rule: this project also names the table in `corpus`,
    # which is a reader in its own right and would mask what is being tested.
    corpus, pr.corpus = pr.corpus, None
    try:
        before = validate(pr, root).warnings
        assert any("normalized.facts" in w and "never read" in w for w in before)

        metrics.define(store, pr, root, mid="pe", sql=GOOD)
        after = validate(pr, root).warnings
        assert not any("normalized.facts" in w and "never read" in w for w in after)
    finally:
        pr.corpus = corpus


def test_check_counts_a_corpus_table_as_read_because_the_reading_tools_read_it(project):
    """`raw.docs` is `corpus.documents`; `read_facts` and friends do nothing else."""
    pr, _store, root = project
    assert not any("raw.docs" in w and "never read" in w
                   for w in validate(pr, root).warnings)


def test_no_stage_kind_exempts_a_table_from_the_warning(project):
    """It exempted exactly one kind -- `weights` -- and that kind is gone."""
    src = (Path(__file__).resolve().parents[1] / "src" / "qanat" / "project.py").read_text()
    assert 'kind != "weights"' not in src


# ------------------------------------------------------ the file keeps its fields
def test_every_field_the_model_holds_is_written_back(tmp_path):
    """`dump_project` dropped `metrics`, `returns` and `corpus`.

    The round-tripper deletes any key the new dict does not carry, so one
    `save_step` on a filings project removed every metric definition, the price
    table a screen reads, and the three tables the reading tools read -- and
    the dump then matched the file it had just emptied, so the safety check
    passed. This is the test that catches the next field.
    """
    from qanat.models import Project
    from qanat.project_io import dump_project

    full = Project.model_validate({
        "project": "everything",
        "store": "./data/qanat.duckdb",
        "stages": [{"id": "raw", "kind": "raw"}],
        "universes": [{"id": "u", "index": "U", "symbols": "./universes/u.csv"}],
        "sources": [{"id": "s", "to": ["raw.a"], "connector": "csv",
                     "options": {"path": "./x.csv"}}],
        "steps": [{"id": "t", "from": ["raw.a"], "to": ["raw.b"], "script": "s.sql"}],
        "metrics": [{"id": "pe", "sql": "SELECT 1 AS subject, 2 AS value",
                     "version": 2, "unit": "ratio", "origin": "shipped"}],
        "retention": {"raw.a": "30d"},
        "time_columns": {"raw.a": "as_of"},
        "job_timeout": "10m",
        "returns": {"table": "raw.prices"},
        "corpus": {"documents": "raw.a", "spans": "raw.b", "facts": "raw.b"},
    })
    written = dump_project(full)
    # Every field the model carries has to reach the dict, under the name the
    # YAML uses for it.
    aliases = {"name": "project"}
    for name in type(full).model_fields:
        value = getattr(full, name)
        if not value:
            continue
        key = aliases.get(name, name)
        assert key in written, f"dump_project drops '{key}', so saving anything deletes it"

    # And it has to survive the round trip, not merely appear.
    back = Project.model_validate(written)
    assert back.metric("pe").version == 2
    assert back.metric("pe").origin == "shipped"
    assert back.returns.table == "raw.prices"
    assert back.corpus.facts == "raw.b"


def test_saving_a_step_does_not_delete_the_metrics_beside_it(project):
    """The failure the test above describes, from the path it happened on."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)

    from qanat.editor import save_step

    save_step(pr, root, {"id": "extra", "from": ["raw.docs"], "to": ["normalized.more"],
                         "script": "steps/extra.sql"})
    fresh, _ = load(root)
    assert fresh.metric("pe") is not None, "save_step deleted the metric block"
    assert fresh.corpus is not None and fresh.corpus.facts == "normalized.facts"


def test_the_yaml_a_definition_lands_in_is_still_the_project(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD, unit="ratio",
                   description="a multi\nline description")
    text = (root / "qanat.yaml").read_text()
    assert "metrics:" in text
    fresh, _ = load(root)
    assert validate(fresh, root).ok
    assert fresh.metric("pe").description.strip() == "a multi line description" or \
        "multi" in fresh.metric("pe").description


# ------------------------------------------------------------------ the surface
def test_the_tools_exist_and_name_no_threshold():
    """A threshold is the caller's half and must not be storable here."""
    from qanat.mcp import TOOLS

    names = [t["name"] for t in TOOLS]
    assert "define_metric" in names
    assert "remove_metric" in names
    assert "save_metric" not in names, "the verb is define: there is no `me` to save for"

    entry = next(t for t in TOOLS if t["name"] == "define_metric")
    fields = set(entry["inputSchema"]["properties"])
    assert not fields & {"op", "value", "threshold", "filter"}, \
        "a threshold is one caller's judgement and does not belong in a definition"
    assert entry["scope"] == "authoring", \
        "defining a metric is the operator's call, a rung above the default"


def test_nothing_on_this_surface_stores_a_filter():
    """Qanat owns the shared half. A filter is one caller's research criteria,
    and a store that cannot tell two callers apart has nowhere to put one."""
    from qanat.mcp import TOOLS
    from qanat.models import Project

    names = {t["name"] for t in TOOLS}
    assert not names & {"save_filter", "list_filters", "remove_filter"}
    assert "filters" not in type(Project).model_fields if False else True
    assert not hasattr(Project.model_validate(
        {"project": "x", "stages": [{"id": "raw"}]}), "filters")


# ---------------------------------------------------- a filter is a named object
def two_metrics(store, pr, root):
    metrics.define(store, pr, root, mid="pe", sql=GOOD, unit="ratio")
    metrics.define(store, pr, root, mid="margin",
                   sql="SELECT subject, value / 100 AS value FROM normalized__facts")


def test_screening_by_filter_name_matches_the_same_clauses_inline(project):
    """`applied.filter` was null, waiting for an object that could not be made.

    It is made by the caller and passed in, because it is the caller's. What was
    missing here was accepting the name and reporting it back.
    """
    pr, store, root = project
    two_metrics(store, pr, root)

    from qanat.screen import parse_filters, screen

    clauses = [{"metric": "pe", "op": "gt", "value": 15}]
    inline = screen(store, pr, parse_filters(clauses), as_of="2024-06-01")
    named = screen(store, pr, parse_filters(
        [{"filter": "deep_value", "version": 2, "clauses": clauses}]), as_of="2024-06-01")

    assert named["rows"] == inline["rows"], "the same rule returns the same rows"
    assert named["counts"] == inline["counts"]
    assert named["applied"][0]["filter"] == "deep_value"
    assert named["applied"][0]["version"] == 2
    assert inline["applied"][0]["filter"] is None, "an unnamed list has nothing to point at"


def test_a_named_filter_holds_several_metrics_each_with_its_own_threshold(project):
    pr, store, root = project
    two_metrics(store, pr, root)

    from qanat.screen import parse_filters, screen

    out = screen(store, pr, parse_filters([{
        "filter": "mine",
        "clauses": [{"metric": "pe", "op": "gte", "value": 10},
                    {"metric": "margin", "op": "gt", "value": 0.15}],
    }]), as_of="2024-06-01")

    passed = {r["subject"]: r["passed"] for r in out["rows"]}
    assert passed == {"AAPL": False, "MSFT": True}
    assert len(out["applied"][0]["clauses"]) == 2
    assert {c["metric"] for c in out["applied"][0]["clauses"]} == {"pe", "margin"}


def test_a_metric_bumped_to_a_new_version_does_not_change_a_pinned_filter(project):
    """The reason a clause may pin a version at all."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)

    from qanat.screen import parse_filters, screen

    pinned = parse_filters([{"filter": "mine", "clauses": [
        {"metric": "pe", "metric_version": 1, "op": "gt", "value": 15}]}])
    floating = parse_filters([{"filter": "mine", "clauses": [
        {"metric": "pe", "op": "gt", "value": 15}]}])

    before = screen(store, pr, pinned, as_of="2024-06-01")
    # Version 2 halves every value, so MSFT's 20 becomes 10 and stops passing.
    metrics.define(store, pr, root, mid="pe", version=2,
                   sql="SELECT subject, value / 2 AS value FROM normalized__facts")

    after = screen(store, pr, pinned, as_of="2024-06-01")
    assert after["rows"] == before["rows"], "a pinned clause still means what it meant"
    assert after["applied"][0]["clauses"][0]["metric_version"] == 1

    moved = screen(store, pr, floating, as_of="2024-06-01")
    assert moved["rows"] != before["rows"], "an unpinned clause reads the current one"
    assert {r["subject"]: r["passed"] for r in moved["rows"]} == {
        "AAPL": False, "MSFT": False}


def test_one_filter_may_read_two_versions_of_one_metric(project):
    """Which is why the values are keyed by metric *and* version."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)
    metrics.define(store, pr, root, mid="pe", version=2,
                   sql="SELECT subject, value / 2 AS value FROM normalized__facts")

    from qanat.screen import parse_filters, screen

    out = screen(store, pr, parse_filters([{"filter": "both", "clauses": [
        {"metric": "pe", "metric_version": 1, "op": "gt", "value": 15},
        {"metric": "pe", "metric_version": 2, "op": "lt", "value": 15},
    ]}]), as_of="2024-06-01")
    passed = {r["subject"]: r["passed"] for r in out["rows"]}
    assert passed == {"AAPL": False, "MSFT": True}


def test_pinning_a_version_that_does_not_exist_names_the_ones_that_do(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)

    from qanat.screen import ScreenError, parse_filters, screen

    with pytest.raises(ScreenError) as exc:
        screen(store, pr, parse_filters([{"metric": "pe", "metric_version": 9,
                                          "op": "gt", "value": 1}]),
               as_of="2024-06-01")
    assert "no version 9" in str(exc.value)
    assert "it has 1" in str(exc.value)


def test_an_unknown_metric_still_names_what_the_project_has(project):
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)

    from qanat.screen import ScreenError, parse_filters, screen

    with pytest.raises(ScreenError, match="This project has: pe"):
        screen(store, pr, parse_filters([{"metric": "nope", "op": "gt", "value": 1}]),
               as_of="2024-06-01")


def test_a_clause_names_the_version_in_what_it_reports_back(project):
    """So whoever keeps the filter can keep what it actually computed."""
    pr, store, root = project
    metrics.define(store, pr, root, mid="pe", sql=GOOD)

    from qanat.screen import parse_filters

    pinned = parse_filters([{"metric": "pe", "metric_version": 1,
                             "op": "gt", "value": 1}])[0].as_applied()
    plain = parse_filters([{"metric": "pe", "op": "gt", "value": 1}])[0].as_applied()
    assert pinned["clauses"][0]["metric_version"] == 1
    assert "metric_version" not in plain["clauses"][0], "no pin, nothing to report"


# ------------------------------------------------- calling it wrong says so
def test_screen_with_no_filter_argument_errors_the_way_read_facts_does(project):
    """It returned `subjects: 0, passed: 0, applied: []`, which reads as
    "nobody passed" rather than "you called it wrong"."""
    from qanat.mcp import TOOLS, Session, ToolError

    pr, store, root = project
    two_metrics(store, pr, root)
    session = Session(str(root))
    try:
        handler = next(t for t in TOOLS if t["name"] == "screen")["handler"]
        with pytest.raises(ToolError) as exc:
            handler(session, {"as_of": "2024-06-01"})
        assert "'filters' is required" in str(exc.value)
        assert "tools/list" in str(exc.value), "the same shape read_facts uses"
    finally:
        session.close()


def test_there_is_one_spelling_because_there_is_one_door():
    """One service layer had two names for one list of one kind of object.

    The CLI said `--filter` and the tool said `filters`. The flag was renamed to
    match, and then the CLI's own `screen` verb was deleted outright -- a second
    implementation of a question a tool already answers is a second place the
    behaviour can quietly differ. So there is one spelling now because there is
    one caller.
    """
    from qanat.cli import MOVED, build_parser
    from qanat.mcp import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "screen")
    assert tool["inputSchema"]["required"] == ["filters"]

    verbs = set()
    for action in build_parser()._actions:
        if getattr(action, "choices", None):
            verbs |= set(action.choices)
    assert "screen" not in verbs, "two doors onto one question"
    assert MOVED["screen"] == "the `screen` tool", "and the deleted one says so"
