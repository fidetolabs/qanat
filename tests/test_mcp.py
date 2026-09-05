"""The agent's door. Parity with the console is the thing being tested."""

from pathlib import Path

from qanat import mcp
from qanat.api import AppState, create_app
from qanat.project import load
from qanat.runner import run_all
from qanat.scaffold import write_project
from qanat.store import Store


def _session(tmp_path: Path) -> mcp.Session:
    write_project(tmp_path, "demo")
    s = mcp.Session(str(tmp_path))
    assert all(r.ok for r in run_all(s.store, s.project, s.root))
    return s


def _call(session: mcp.Session, name: str, args: dict | None = None):
    entry = next(t for t in mcp.TOOLS if t["name"] == name)
    return entry["handler"](session, args or {})


# ---------------------------------------------------------------------- shape
def test_every_tool_declares_a_schema():
    assert mcp.TOOLS
    for t in mcp.TOOLS:
        assert t["name"] and t["description"]
        schema = t["inputSchema"]
        assert schema["type"] == "object"
        for req in schema["required"]:
            assert req in schema["properties"], f"{t['name']}: '{req}' required but not described"


def test_read_only_hides_every_tool_that_writes():
    writers = {t["name"] for t in mcp.TOOLS if t["writes"]}
    assert {"run", "backtest", "save_step"} <= writers
    readers = {t["name"] for t in mcp.TOOLS if not t["writes"]}
    assert writers.isdisjoint(readers)


def test_initialize_and_list_speak_the_protocol(tmp_path: Path):
    session = _session(tmp_path)
    tools = list(mcp.TOOLS)

    init = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                   "params": {"protocolVersion": "2024-11-05"}}, tools)
    assert init["result"]["serverInfo"]["name"] == "qanat"
    assert init["result"]["protocolVersion"] == "2024-11-05"

    assert mcp._dispatch(session, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                         tools) is None

    listed = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, tools)
    assert {t["name"] for t in listed["result"]["tools"]} == {t["name"] for t in tools}
    session.close()


def test_an_unknown_tool_answers_instead_of_dying(tmp_path: Path):
    session = _session(tmp_path)
    out = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                  "params": {"name": "nope"}}, list(mcp.TOOLS))
    assert out["result"]["isError"]
    assert "Available:" in out["result"]["content"][0]["text"]
    session.close()


# -------------------------------------------------------------------- answers
def test_discovery_needs_no_documentation(tmp_path: Path):
    session = _session(tmp_path)
    tables = _call(session, "list_tables")["tables"]
    assert {t["ref"] for t in tables} == set(session.project.tables())

    described = _call(session, "describe_table", {"ref": "normalized.prices"})
    assert described["rows"] > 0
    assert described["time_column"] == "date"
    assert any(c["name"] == "close" for c in described["columns"])
    session.close()


def test_an_error_says_what_to_do_next(tmp_path: Path):
    session = _session(tmp_path)
    for name, args in (("describe_table", {"ref": "nope.nope"}),
                       ("read_step", {"id": "nope"}),
                       ("report", {"run_id": 1})):
        try:
            _call(session, name, args)
        except mcp.ToolError as exc:
            assert len(str(exc)) > 20, f"{name}: error is too thin to act on"
        else:
            raise AssertionError(f"{name} should have raised")
    session.close()


def test_sample_table_honours_as_of(tmp_path: Path):
    session = _session(tmp_path)
    full = _call(session, "sample_table", {"ref": "normalized.prices", "limit": 10000})
    newest = max(r["date"] for r in full["sample"])
    cut = _call(session, "sample_table",
                {"ref": "normalized.prices", "limit": 10000, "as_of": "2020-01-01"})
    assert cut["rows"] == 0 < full["rows"]
    assert newest  # the unfiltered read did see something
    session.close()


def test_lineage_knows_what_breaks_the_portfolio(tmp_path: Path):
    session = _session(tmp_path)
    out = _call(session, "lineage", {"ref": "raw.daily_prices"})
    assert "normalized.prices" in out["reaches"]
    assert out["breaks_portfolio"] is True
    assert _call(session, "lineage", {"ref": "weights.target"})["breaks_portfolio"] is False
    session.close()


def test_check_is_not_cli_only(tmp_path: Path):
    """Parity: the same answer through MCP and through the HTTP API."""
    from fastapi.testclient import TestClient

    session = _session(tmp_path)
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    client = TestClient(create_app(AppState(store=store, project=project, root=root, sched=None)))

    over_http = client.get("/api/check").json()
    over_mcp = _call(session, "check")
    assert over_http == over_mcp
    assert over_http["ok"] is True
    store.close()
    session.close()


def test_the_shelf_installs_an_alpha_that_runs(tmp_path: Path):
    """A shelf alpha has to become an ordinary step -- one that the contract accepts
    and the runner can execute -- or it is a demo, not a starting point."""
    from qanat.project import validate

    session = _session(tmp_path)
    names = {a["name"] for a in _call(session, "list_alphas")["alphas"]}
    assert {"momentum", "reversal", "low_vol", "neutral_momentum"} <= names

    # an alpha is its own DAG now, so adding one leaves the others in place
    before = {a for a, _ in session.project.alphas}
    out = _call(session, "use_alpha", {"name": "neutral_momentum", "reads": "normalized.prices"})
    assert out["installed"] == "alpha_neutral_momentum"
    assert out["writes"] == "weights.neutral_momentum"
    session.reload()
    after = {a for a, _ in session.project.alphas}
    assert before < after, "adding an alpha must not remove the one that was there"

    session.reload()
    assert validate(session.project, session.root).ok
    results = {r.job_id: r for r in run_all(session.store, session.project, session.root)}
    assert results["alpha_neutral_momentum"].ok, results["alpha_neutral_momentum"].error

    weights = session.store.read("weights.neutral_momentum")
    assert abs(weights["weight"].abs().sum() - 1.0) < 1e-6   # book size is fixed
    assert abs(weights["weight"].sum()) < 1e-6               # and it is market neutral
    session.close()


def test_conditions_are_asked_for_not_assumed(tmp_path: Path):
    session = _session(tmp_path)
    c = _call(session, "backtest_conditions")
    assert set(c["ask_the_person_for"]) == {
        "alpha", "from", "to", "universe", "rebalance", "decay", "split"}
    assert c["alphas_in_this_project"]
    assert c["universe"]["declared_on_steps"] == ["demo8"]
    assert c["window"]["data_available"]["latest"]
    session.close()


def test_the_console_and_the_agent_agree_about_what_is_stale(tmp_path: Path):
    """Two doors on one engine. If they can disagree about which tables stopped
    being current, one of them is lying to somebody."""
    from fastapi.testclient import TestClient

    session = _session(tmp_path)
    script = tmp_path / "steps" / "normalize.sql"
    script.write_text(script.read_text() + "\n-- moved\n")
    session.reload()

    over_mcp = set(_call(session, "stale_tables")["stale"])

    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    client = TestClient(create_app(AppState(store=store, project=project, root=root, sched=None)))
    graph = client.get("/api/graph").json()
    over_http = {t["ref"] for t in graph["tables"] if t.get("stale")}

    assert over_mcp == over_http, f"mcp says {over_mcp}, the console says {over_http}"
    assert "normalized.prices" in over_mcp
    store.close()
    session.close()
