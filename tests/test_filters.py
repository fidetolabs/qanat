"""The operator's saved filters: `filters.yaml`, run by name.

A filter is saved clauses for the one screen query. These hold the three things
that make it that and nothing more: it screens exactly as its clauses do inline,
it writes no table and no definition, and a call naming it and passing clauses
too is refused rather than merged.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from qanat import filters, mcp
from qanat.filters import FilterError
from qanat.models import Project
from qanat.project import load
from qanat.screen import ScreenError, run
from qanat.store import Store

QANAT_YAML = """\
project: t
store: ./data/q.duckdb
stages:
  - {id: raw, kind: raw}
  - {id: metrics, kind: metrics}
time_columns:
  metrics.pe: date
  metrics.roe: date
"""

JOBS_YAML = """\
ingestion_job_definitions:
  - {id: prices, to: [raw.prices], connector: seed, key: [subject, as_of],
     options: {path: ./seed/prices.csv}}
transform_job_definitions:
  - {id: pe, from: [raw.prices], to: [metrics.pe], script: transforms/metrics/pe.sql,
     key: [subject, date]}
  - {id: roe, from: [raw.prices], to: [metrics.roe], script: transforms/metrics/roe.sql,
     key: [subject, date]}
"""


@pytest.fixture
def project(tmp_path: Path) -> tuple[Project, Path, Store]:
    (tmp_path / "qanat.yaml").write_text(QANAT_YAML)
    (tmp_path / "jobs.yaml").write_text(JOBS_YAML)
    (tmp_path / "transforms" / "metrics").mkdir(parents=True)
    for m in ("pe", "roe"):
        (tmp_path / "transforms" / "metrics" / f"{m}.sql").write_text(
            "SELECT subject, as_of AS date, close AS value FROM raw__prices")
    (tmp_path / "data").mkdir()
    p, root = load(tmp_path)
    s = Store(p.store_url(root))
    dates = pd.to_datetime(["2023-01-01", "2024-01-01"] * 3)
    s.write("raw.prices", pd.DataFrame({
        "subject": ["A", "A", "B", "B", "C", "C"], "as_of": dates,
        "close": [1.0] * 6}), mode="replace")
    s.write("metrics.pe", pd.DataFrame({
        "subject": ["A", "A", "B", "B", "C", "C"], "date": dates,
        "value": [5.0, 8.0, 50.0, 9.0, 12.0, 30.0]}),
        mode="replace", key=["subject", "date"])
    s.write("metrics.roe", pd.DataFrame({
        "subject": ["A", "A", "B", "B", "C", "C"], "date": dates,
        "value": [0.2, 0.3, 0.1, 0.05, 0.25, 0.4]}),
        mode="replace", key=["subject", "date"])
    yield p, root, s
    s.close()


CHEAP_AND_PROFITABLE = [{"metric": "pe", "op": "<", "value": 15},
                        {"metric": "roe", "op": ">=", "value": 0.2}]


def passed(out: dict) -> list[str]:
    return sorted(r["subject"] for r in out["rows"] if r["passed"])


def test_a_saved_filter_screens_the_same_as_its_clauses_inline(project):
    p, root, s = project
    filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE, description="cheap and good")

    by_name = run(s, p, filter="cheap", as_of="2023-06-01")
    inline = run(s, p, clauses=CHEAP_AND_PROFITABLE, as_of="2023-06-01")

    assert passed(by_name) == passed(inline) == ["A", "C"]
    assert by_name["filter"] == "cheap"
    assert inline["filter"] is None
    assert by_name["applied"] == inline["applied"]


def test_a_screen_with_both_or_neither_is_refused(project):
    p, root, s = project
    filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE)
    with pytest.raises(ScreenError, match="not both"):
        run(s, p, filter="cheap", clauses=CHEAP_AND_PROFITABLE, as_of="2023-06-01")
    with pytest.raises(ScreenError, match="needs `clauses`"):
        run(s, p, as_of="2023-06-01")


def test_a_filter_writes_no_table_and_no_definition(project):
    p, root, s = project
    tables_before = set(s.tables()) if hasattr(s, "tables") else None
    jobs_before = yaml.safe_load((root / "jobs.yaml").read_text())
    defs_before = [d.id for d in p.definitions]

    filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE)
    run(s, p, filter="cheap", as_of="2023-06-01")

    assert [d.id for d in p.definitions] == defs_before
    assert yaml.safe_load((root / "jobs.yaml").read_text()) == jobs_before
    if tables_before is not None:
        assert set(s.tables()) == tables_before
    saved = yaml.safe_load((root / "filters.yaml").read_text())
    assert saved == {"filters": [{"id": "cheap", "clauses": CHEAP_AND_PROFITABLE}]}


def test_a_filter_naming_a_metric_nobody_defined_is_refused_at_save(project):
    p, root, _ = project
    with pytest.raises(FilterError, match="no metric called debt"):
        filters.save(p, root, "x", [{"metric": "debt", "op": "<", "value": 1}])
    assert not (root / "filters.yaml").exists()


def test_a_bad_comparison_or_an_empty_filter_is_refused_at_save(project):
    p, root, _ = project
    with pytest.raises(FilterError, match="no comparison"):
        filters.save(p, root, "x", [{"metric": "pe", "op": "about", "value": 1}])
    with pytest.raises(FilterError, match="at least one clause"):
        filters.save(p, root, "x", [])


def test_running_a_filter_that_is_not_saved_names_the_ones_that_are(project):
    p, root, s = project
    filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE)
    with pytest.raises(ScreenError, match="has: cheap"):
        run(s, p, filter="dear", as_of="2023-06-01")


def test_saving_again_replaces_and_removing_leaves_no_file(project):
    p, root, _ = project
    filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE)
    out = filters.save(p, root, "cheap", CHEAP_AND_PROFITABLE[:1])
    assert out["replaced"] is True
    assert filters.clauses_of(p, "cheap") == CHEAP_AND_PROFITABLE[:1]

    reloaded, _ = load(root)
    assert [f.id for f in reloaded.filters] == ["cheap"]

    filters.remove(p, root, "cheap")
    assert filters.listing(p) == []
    assert not (root / "filters.yaml").exists(), "an empty part file is removed"


def test_a_project_with_no_filters_file_loads_with_none(project):
    p, _, _ = project
    assert filters.listing(p) == []


def test_reading_a_filter_is_research_and_writing_one_is_authoring():
    research = {t["name"] for t in mcp.tools_for("research")}
    full = {t["name"] for t in mcp.tools_for("full")}
    authoring = {t["name"] for t in mcp.tools_for("authoring")}
    assert "list_filters" in research
    assert "save_filter" not in full and "remove_filter" not in full
    assert {"save_filter", "remove_filter"} <= authoring


def test_the_tools_round_trip_through_the_session(project):
    _p, root, _ = project
    session = mcp.Session(str(root))
    offered = mcp.tools_for("authoring")

    def call(name: str, **args):
        out = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}},
                            offered)
        return out["result"]

    try:
        saved = call("save_filter", id="cheap", clauses=CHEAP_AND_PROFITABLE)
        assert not saved.get("isError"), saved
        listed = call("list_filters")
        assert '"cheap"' in listed["content"][0]["text"]
        ran = call("screen_companies", filter="cheap", as_of="2023-06-01")
        assert not ran.get("isError"), ran
        both = call("screen_companies", filter="cheap", clauses=CHEAP_AND_PROFITABLE,
                    as_of="2023-06-01")
        assert both.get("isError")
    finally:
        session.close()
