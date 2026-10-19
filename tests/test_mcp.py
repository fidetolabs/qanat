"""The agent's door. Parity with the engine underneath is the thing being tested."""

from pathlib import Path

import pytest
from conftest import write_demo_project as write_project

from qanat import mcp
from qanat.project import load
from qanat.runner import run_all
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


# ---------------------------------------------------------------------- scopes
def test_every_tool_names_one_of_the_three_scopes():
    assert mcp.SCOPES == ("data", "research", "full")
    for t in mcp.TOOLS:
        assert t["scope"] in mcp.SCOPES, f"{t['name']}: scope {t['scope']!r}"


def test_a_tool_cannot_be_declared_without_a_scope():
    with pytest.raises(TypeError):
        mcp.tool("nope", "no scope given", {"properties": {}})


def test_an_unknown_scope_says_what_the_three_are():
    with pytest.raises(mcp.ScopeError) as exc:
        mcp.tools_for("readonly")
    assert "data, research, full" in str(exc.value)


def test_the_scopes_nest():
    data, research, full = (
        [t["name"] for t in mcp.tools_for(s)] for s in mcp.SCOPES)
    assert set(data) < set(research) < set(full)
    assert len(full) == len(mcp.TOOLS)
    # whatever the scope, the order a caller sees is the declaration order
    assert data == [n for n in full if n in set(data)]
    assert research == [n for n in full if n in set(research)]


def test_data_offers_nothing_that_writes():
    assert not [t["name"] for t in mcp.tools_for("data") if t["writes"]]


def test_research_never_writes_the_project_file():
    """A screen writes nothing at all, so `research` has no writers left. What
    the scope line still has to hold is that nothing in it edits qanat.yaml."""
    assert not {t["name"] for t in mcp.tools_for("research") if t["writes"]}
    # Anything that edits qanat.yaml is authoring, so a research caller cannot
    # reach it: a hosted caller may screen and may not rewrite the project
    assert {"save_step", "save_source", "run"} <= {
        t["name"] for t in mcp.TOOLS if t["scope"] == "full"}


def test_a_tool_above_the_scope_says_so_instead_of_denying_it_exists(tmp_path: Path):
    session = _session(tmp_path)
    reply = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                    "params": {"name": "save_step", "arguments": {}}},
                          mcp.tools_for("data"))
    body = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert "'full' scope" in body and "--scope full" in body
    session.close()


def test_a_tool_that_does_not_exist_still_says_that(tmp_path: Path):
    session = _session(tmp_path)
    reply = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                    "params": {"name": "teleport", "arguments": {}}},
                          mcp.tools_for("data"))
    assert "no tool called 'teleport'" in reply["result"]["content"][0]["text"]
    session.close()


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
                       ("screen", {"filters": [{"metric": "nope", "op": "<", "value": 1}]})):
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


def test_check_is_not_cli_only(tmp_path: Path):
    """Parity: the tool answers exactly what the contract check answers.

    This used to compare MCP against a second HTTP surface. There is one door
    now, so the thing worth comparing against is the function underneath -- which
    is what that surface was calling too.
    """
    from qanat.project import validate

    session = _session(tmp_path)
    rep = validate(session.project, session.root)
    over_mcp = _call(session, "check")

    assert over_mcp == {"ok": rep.ok, "errors": rep.errors, "warnings": rep.warnings}
    assert over_mcp["ok"] is True
    session.close()


def test_list_steps_filters_by_stage_and_adds_net(tmp_path: Path):
    """The shelf is gone and nothing replaced it. What produces the weights tables
    has no name of its own, so it is found by filtering on the stage that
    identifies it -- and `net` is what each one has earned."""
    from qanat.project import validate

    session = _session(tmp_path)
    assert validate(session.project, session.root).ok
    assert all(r.ok for r in run_all(session.store, session.project, session.root))

    everything = _call(session, "list_steps")
    assert everything["sources"], "unfiltered, the sources come too"

    only = _call(session, "list_steps", {"stage": "features"})
    assert only["sources"] == [], "a stage filter is about steps, so sources drop out"
    ids = {s["id"] for s in only["steps"]}
    expected = {st.id for st in session.project.steps
                if any(w.startswith("features.") for w in st.writes)}
    assert ids == expected and ids

    with_net = _call(session, "list_steps", {"stage": "features", "net": True})
    assert "net" in with_net["steps"][0]
    session.close()

def test_the_picture_and_the_agent_agree_about_what_is_stale(tmp_path: Path):
    """Two readers of one engine. If they can disagree about which tables stopped
    being current, one of them is lying to somebody.

    `build_graph` is what `qanat graph` draws from, and what `lineage` answers
    out of; the comparison is the same one either way.
    """
    from qanat.graph import build_graph

    session = _session(tmp_path)
    script = tmp_path / "steps" / "normalize.sql"
    script.write_text(script.read_text() + "\n-- moved\n")
    session.reload()

    over_mcp = set(_call(session, "stale_tables")["stale"])

    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    graph = build_graph(store, project, root, None)
    in_the_picture = {t["ref"] for t in graph["tables"] if t.get("stale")}

    assert over_mcp == in_the_picture, f"mcp says {over_mcp}, the graph says {in_the_picture}"
    assert "normalized.prices" in over_mcp
    store.close()
    session.close()


