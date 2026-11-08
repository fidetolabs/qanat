"""The web door: pages and a JSON API over the same service layer as `/mcp`.

What these hold is the plan's A5 and the scope rule: every route answers what
the matching tool answers, a write is refused below the scope its tool needs,
and the web layer holds no SQL of its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qanat.mcp_http import create_app
from qanat.store import Store

QANAT_YAML = """\
project: webtest
store: ./data/q.duckdb
stages:
  - {id: raw, kind: raw}
  - {id: normalized, kind: normalized}
  - {id: metrics, kind: metrics}
corpus: {documents: raw.documents, spans: normalized.spans, facts: normalized.facts}
time_columns:
  metrics.pe: date
  metrics.roe: date
"""

JOBS_YAML = """\
ingestion_job_definitions:
  - {id: documents, to: [raw.documents], connector: seed, key: [id],
     options: {path: ./seed/documents.csv}}
  - {id: prices, to: [raw.prices], connector: seed, key: [subject, as_of],
     options: {path: ./seed/prices.csv}}
transform_job_definitions:
  - {id: parse_facts, from: [raw.documents], to: [normalized.facts],
     script: transforms/parse_facts.sql}
  - {id: split_sections, from: [raw.documents], to: [normalized.spans],
     script: transforms/split_sections.sql}
  - {id: pe, from: [raw.prices], to: [metrics.pe], script: transforms/metrics/pe.sql,
     key: [subject, date]}
  - {id: roe, from: [normalized.facts], to: [metrics.roe],
     script: transforms/metrics/roe.sql, key: [subject, date]}
"""

CIK = "0000320193"


def build(root: Path) -> Path:
    """A project on disk with a corpus, two metrics and prices, already written."""
    (root / "qanat.yaml").write_text(QANAT_YAML)
    (root / "jobs.yaml").write_text(JOBS_YAML)
    (root / "transforms" / "metrics").mkdir(parents=True)
    for name in ("parse_facts", "split_sections"):
        (root / "transforms" / f"{name}.sql").write_text("SELECT * FROM raw__documents")
    (root / "transforms" / "metrics" / "pe.sql").write_text(
        "SELECT subject, as_of AS date, close AS value FROM raw__prices")
    (root / "transforms" / "metrics" / "roe.sql").write_text(
        "SELECT document AS subject, as_of AS date, value FROM normalized__facts")
    (root / "data").mkdir()
    s = Store(str(root / "data" / "q.duckdb"))
    keys = json.dumps({"ticker": "AAPL", "company_name": "Apple Inc.", "cik": CIK})
    s.write("raw.documents", pd.DataFrame({
        "id": ["A25", "A24"], "entity": [CIK, CIK], "doc_type": ["10-K", "10-K"],
        "published_at": pd.to_datetime(["2025-11-01", "2024-11-01"]),
        "period_end": pd.to_datetime(["2025-09-27", "2024-09-28"]),
        "source_keys": [keys, keys]}), mode="replace")
    s.write("normalized.spans", pd.DataFrame({
        "document": ["A25", "A25", "A24"], "section": ["Item 1", "Item 7", "Item 1"],
        "char_start": [0, 500, 0], "char_end": [500, 900, 300],
        "n_chars": [500, 400, 300], "rule": ["ordered", "title", "ordered"],
        "text": ["Item 1. Business", "Item 7. MD&A", "Item 1. Business"],
        "as_of": pd.to_datetime(["2025-11-01", "2025-11-01", "2024-11-01"])}),
        mode="replace")
    s.write("normalized.facts", pd.DataFrame({
        "document": ["A25", "A24"], "concept": ["us-gaap:Revenues"] * 2,
        "dims": ["{}"] * 2,
        "period_start": pd.to_datetime(["2024-09-29", "2023-10-01"]),
        "period_end": pd.to_datetime(["2025-09-27", "2024-09-28"]),
        "is_instant": [False, False], "value": [391_035e6, 383_285e6],
        "unit": ["iso4217:USD"] * 2, "decimals": ["-6"] * 2,
        "as_of": pd.to_datetime(["2025-11-01", "2024-11-01"])}), mode="replace")
    s.write("raw.prices", pd.DataFrame({
        "subject": [CIK, CIK, "0000789019", "0000789019"],
        "as_of": pd.to_datetime(["2024-11-01", "2025-11-01"] * 2),
        "close": [10.0, 12.0, 40.0, 35.0]}), mode="replace")
    s.write("metrics.pe", pd.DataFrame({
        "subject": [CIK, CIK, "0000789019", "0000789019"],
        "date": pd.to_datetime(["2024-11-01", "2025-11-01"] * 2),
        "value": [28.0, 31.0, 35.0, 33.0]}), mode="replace", key=["subject", "date"])
    s.write("metrics.roe", pd.DataFrame({
        "subject": [CIK, CIK, "0000789019", "0000789019"],
        "date": pd.to_datetime(["2024-11-01", "2025-11-01"] * 2),
        "value": [1.5, 1.6, 0.35, 0.33]}), mode="replace", key=["subject", "date"])
    s.close()
    return root


def client(root: Path, scope: str = "authoring", token: str | None = None) -> TestClient:
    return TestClient(create_app(str(root), scope, token=token, web=True),
                      base_url="http://127.0.0.1")


@pytest.fixture
def web(tmp_path: Path) -> TestClient:
    c = client(build(tmp_path))
    yield c
    c.app.state.qanat_session.close()


def test_the_page_and_its_files_are_served(web):
    assert "<title>qanat</title>" in web.get("/").text
    assert web.get("/static/app.js").status_code == 200
    assert web.get("/static/../api.py").status_code == 404


def test_health_says_what_this_scope_can_do(web):
    h = web.get("/api/health").json()
    assert h["project"] == "webtest" and h["scope"] == "authoring"
    assert h["can"]["save_filter"] and h["can"]["define_metric"]


def test_without_web_there_are_no_pages(tmp_path):
    c = TestClient(create_app(str(build(tmp_path)), "authoring"),
                   base_url="http://127.0.0.1")
    try:
        assert c.get("/api/health").status_code == 404
    finally:
        c.app.state.qanat_session.close()


def test_a_write_below_its_tools_scope_is_refused(tmp_path):
    c = client(build(tmp_path), scope="research")
    try:
        assert c.get("/api/health").json()["can"]["save_filter"] is False
        r = c.put("/api/filters/x", json={"clauses": [{"metric": "pe", "op": "<", "value": 30}]})
        assert r.status_code == 403 and "--scope authoring" in r.json()["detail"]
        assert c.get("/api/filters").status_code == 200
        assert c.post("/api/screen", json={"clauses": [{"metric": "pe", "op": "<", "value": 30}],
                                           "as_of": "2025-12-01"}).status_code == 200
    finally:
        c.app.state.qanat_session.close()


def test_the_page_shell_passes_without_the_token_and_the_api_does_not(tmp_path):
    c = client(build(tmp_path), token="sekrit")
    try:
        assert c.get("/").status_code == 200
        assert c.get("/static/app.css").status_code == 200
        assert c.get("/api/health").status_code == 401
        ok = c.get("/api/health", headers={"Authorization": "Bearer sekrit"})
        assert ok.status_code == 200
    finally:
        c.app.state.qanat_session.close()


def test_a_filter_saved_on_the_page_is_the_one_mcp_lists(web):
    clauses = [{"metric": "pe", "op": "<", "value": 32}, {"metric": "roe", "op": ">", "value": 1}]
    saved = web.put("/api/filters/cheap", json={"clauses": clauses, "description": "d"})
    assert saved.status_code == 200, saved.text

    from qanat import mcp

    session = web.app.state.qanat_session
    out = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "list_filters", "arguments": {}}},
                        mcp.tools_for("authoring"))
    assert json.loads(out["result"]["content"][0]["text"])["filters"][0]["id"] == "cheap"

    by_name = web.post("/api/screen", json={"filter": "cheap", "as_of": "2025-12-01"}).json()
    inline = web.post("/api/screen", json={"clauses": clauses, "as_of": "2025-12-01"}).json()
    assert by_name["filter"] == "cheap" and inline["filter"] is None
    assert [r["subject"] for r in by_name["rows"] if r["passed"]] == [CIK]
    assert by_name["rows"] == inline["rows"]

    both = web.post("/api/screen", json={"filter": "cheap", "clauses": clauses,
                                         "as_of": "2025-12-01"})
    assert both.status_code == 400 and "not both" in both.json()["detail"]
    assert web.delete("/api/filters/cheap").status_code == 200
    assert web.get("/api/filters").json()["filters"] == []


def test_what_i_hold_shows_each_filing_and_its_stages(web):
    cos = web.get("/api/companies").json()
    assert cos["companies"][0]["ticker"] == "AAPL"
    p = web.get("/api/companies/AAPL/filings").json()
    assert [f["document"] for f in p["filings"]] == ["A25", "A24"]
    newest = p["filings"][0]
    assert newest["raw"] is True and newest["facts"] == 1 and newest["sections"] == 2
    assert newest["metrics"] == ["pe", "roe"]
    assert p["metrics"] == ["pe", "roe"]


def test_a_value_traces_back_to_the_filing_under_it(web):
    t = web.get("/api/metrics/pe/trace?company=AAPL&as_of=2025-12-01").json()
    assert t["value"] == 31.0
    assert t["computed_from"] == [{"from": "raw.prices", "definition": "pe"}]
    assert t["filing"]["document"] == "A25"
    assert t["facts"]["facts"][0]["concept"] == "us-gaap:Revenues"


def test_metrics_list_define_read_back_and_remove(web):
    assert {m["id"] for m in web.get("/api/metrics").json()["metrics"]} == {"pe", "roe"}
    sql = "SELECT subject, as_of AS date, close * 2 AS value FROM raw__prices"
    r = web.post("/api/metrics", json={"id": "double", "sql": sql, "unit": "USD"})
    assert r.status_code == 200, r.text
    assert web.get("/api/metrics/double/formula").json()["sql"].strip() == sql
    assert "double" in {m["id"] for m in web.get("/api/metrics").json()["metrics"]}
    bad = web.post("/api/metrics", json={"id": "bad", "sql": "SELECT nope FROM nowhere"})
    assert bad.status_code == 400
    asked = web.delete("/api/metrics/double")
    assert asked.status_code == 400 and "no filter saved" in asked.json()["detail"]
    gone = web.delete("/api/metrics/double?force=true")
    assert gone.status_code == 200, gone.text


def test_the_table_reader_honours_as_of(web):
    tables = {t["ref"] for t in web.get("/api/tables").json()["tables"]}
    assert {"raw.documents", "metrics.pe"} <= tables
    now = web.get("/api/tables/metrics.pe").json()
    then = web.get("/api/tables/metrics.pe?as_of=2025-01-01").json()
    assert now["sample"]["rows"] == 4 and then["sample"]["rows"] == 2
    assert web.get("/api/tables/metrics.pe/profile").status_code == 200
    assert web.get("/api/tables/metrics.pe/trace").json()["upstream"][0]["from"] == "raw.prices"
    assert web.get("/api/tables/raw.nope").status_code == 400


def test_coverage_definitions_and_executions_answer(web):
    assert web.get("/api/coverage").json()["to"]
    defs = web.get("/api/definitions").json()
    assert {d["id"] for d in defs["transforms"]} >= {"pe", "roe"}
    assert "executions" in web.get("/api/executions").json()


def test_an_unknown_route_is_a_404_not_the_page(web):
    assert web.get("/api/nope").status_code == 404


def test_the_web_layer_holds_no_sql():
    """A5. A route calls one service function; the store is only handed over."""
    api = (Path(__file__).parent.parent / "src" / "qanat" / "web" / "api.py").read_text()
    assert not re.search(r"\b(SELECT|INSERT|UPDATE|DELETE FROM|JOIN)\b", api)
    assert not re.search(r"store\.\w+\(", api), "a route reached into the store"
