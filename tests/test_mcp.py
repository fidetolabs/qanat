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
def test_every_tool_names_one_of_the_scopes():
    assert mcp.SCOPES == ("data", "research", "full", "authoring")
    for t in mcp.TOOLS:
        assert t["scope"] in mcp.SCOPES, f"{t['name']}: scope {t['scope']!r}"


def test_the_default_scope_is_not_the_widest_one():
    """`authoring` is reached by asking for it.

    The pipeline from a filing to a metric is the same in every project, so
    nobody screening filings needs the tools that rewire it -- and carrying
    them means an agent can rewire it by accident.
    """
    assert mcp.DEFAULT_SCOPE == "full"
    assert mcp.DEFAULT_SCOPE != mcp.SCOPES[-1]
    offered = {t["name"] for t in mcp.tools_for(mcp.DEFAULT_SCOPE)}
    assert not offered & {"save_step", "remove_step", "save_source"}
    # And a caller who asks for the authoring scope still gets all three.
    assert {"save_step", "remove_step", "save_source"} <= {
        t["name"] for t in mcp.tools_for("authoring")}


def test_a_tool_cannot_be_declared_without_a_scope():
    with pytest.raises(TypeError):
        mcp.tool("nope", "no scope given", {"properties": {}})


def test_an_unknown_scope_says_what_the_three_are():
    with pytest.raises(mcp.ScopeError) as exc:
        mcp.tools_for("readonly")
    assert "data, research, full" in str(exc.value)


def test_the_scopes_nest():
    data, research, full, authoring = (
        [t["name"] for t in mcp.tools_for(s)] for s in mcp.SCOPES)
    assert set(data) < set(research) < set(full) < set(authoring)
    assert len(authoring) == len(mcp.TOOLS)
    # whatever the scope, the order a caller sees is the declaration order
    for narrower in (data, research, full):
        assert narrower == [n for n in authoring if n in set(narrower)]


def test_data_offers_nothing_that_writes():
    assert not [t["name"] for t in mcp.tools_for("data") if t["writes"]]


def test_research_never_writes_the_project_file():
    """A screen writes nothing at all, so `research` has no writers left. What
    the scope line still has to hold is that nothing in it edits qanat.yaml."""
    assert not {t["name"] for t in mcp.tools_for("research") if t["writes"]}
    # Anything that rewires the pipeline is authoring; running it is `full`.
    # A hosted caller may screen, and may not rewrite the project.
    by_scope = {t["name"]: t["scope"] for t in mcp.TOOLS}
    assert by_scope["save_step"] == "authoring"
    assert by_scope["save_source"] == "authoring"
    assert by_scope["remove_step"] == "authoring"
    assert by_scope["run"] == "full"


def test_a_tool_above_the_scope_says_so_instead_of_denying_it_exists(tmp_path: Path):
    session = _session(tmp_path)
    reply = mcp._dispatch(session, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                    "params": {"name": "save_step", "arguments": {}}},
                          mcp.tools_for("data"))
    body = reply["result"]["content"][0]["text"]
    assert reply["result"]["isError"] is True
    assert "'authoring' scope" in body and "--scope authoring" in body
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


def test_list_steps_filters_by_stage(tmp_path: Path):
    """The shelf is gone and nothing replaced it. What produces a given table has
    no name of its own, so it is found by filtering on the stage that identifies
    it."""
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




# ------------------------------------------------- the surface stops offering it
def test_the_cli_is_four_verbs_and_nothing_else():
    """It was ten. Six of them were a person's door onto a question a tool
    already answers, which is a second place the behaviour can differ."""
    from qanat.cli import build_parser

    verbs = set()
    for action in build_parser()._actions:
        if getattr(action, "choices", None):
            verbs |= set(action.choices)
    assert verbs == {"init", "check", "mcp", "schedule"}


def test_every_removed_command_names_what_replaced_it():
    from qanat.cli import MOVED, main

    assert set(MOVED) == {"ls", "graph", "plan", "prune", "run", "screen", "serve"}
    for name, instead in MOVED.items():
        assert instead, f"{name} says nothing about where it went"
        assert main([name]) == 2, f"{name} should exit 2, not run"
    # And `graph` is the one named as having no replacement.
    assert "nothing" in MOVED["graph"]


def test_a_removed_command_is_still_caught_behind_a_project_flag():
    """`qanat -p ./proj graph` puts a path in front of the verb."""
    from qanat.cli import _verb, main

    assert _verb(["-p", "/tmp/anything", "graph"]) == "graph"
    assert _verb(["--project=/tmp", "ls"]) == "ls"
    assert main(["-p", "/tmp/anything", "graph"]) == 2


def test_the_clock_and_the_server_are_each_one_command():
    """`serve` ran both and had no way to run the clock alone -- backwards for
    a recorder that wants a clock and no open port, and for a hosted reader
    over a store somebody else fills."""
    from qanat.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if getattr(a, "choices", None))

    # The clock, serving nothing.
    schedule = sub.choices["schedule"]
    flags = {opt for a in schedule._actions for opt in a.option_strings}
    assert "--port" not in flags and "--host" not in flags, "it serves nothing"
    assert "--workers" in flags

    # The server, with no clock unless one is asked for.
    mcp_flags = {opt for a in sub.choices["mcp"]._actions for opt in a.option_strings}
    assert "--http" in mcp_flags
    assert "--schedule" in mcp_flags, "one machine may still do both jobs"
    assert "--no-schedule" not in mcp_flags, "the clock is opt-in, not opt-out"


def test_a_clock_over_stdio_is_refused_with_the_lifetime_reason(capsys):
    """Not a rule about authority: a thread started here dies with the client."""
    from qanat.cli import main

    assert main(["mcp", "--schedule"]) == 2
    said = capsys.readouterr().err
    assert "dies with the conversation" in said
    assert "qanat schedule" in said


def test_retention_still_runs_on_the_schedulers_clock():
    """It was checked and is live, so it is not on the deletion list."""
    import inspect

    from qanat.scheduler import Scheduler

    loop = inspect.getsource(Scheduler._loop)
    assert "run_retention" in loop
    assert "p.retention" in loop or "project.retention" in loop


def test_lineage_and_stale_tables_are_still_in_data_unmoved():
    """Both only mean something to a caller who may change the pipeline, so
    both look like they belong above the default too -- and `data` is a
    published contract. Taking a tool out of one is worse than adding one.
    Moving them is a separate decision, not a cleanup."""
    by_scope = {t["name"]: t["scope"] for t in mcp.TOOLS}
    assert by_scope["lineage"] == "data"
    assert by_scope["stale_tables"] == "data"


def test_lineages_description_names_no_removed_concept():
    entry = next(t for t in mcp.TOOLS if t["name"] == "lineage")
    assert "weights" not in entry["description"]
    assert "pnl" not in entry["description"]


def test_prune_is_a_flag_on_the_tool_that_already_finds_the_orphans(tmp_path: Path):
    """The one irreversible verb does not belong at the top level, and folding
    it in means the list and the action come from one call."""
    entry = next(t for t in mcp.TOOLS if t["name"] == "plan")
    assert "prune" in entry["inputSchema"]["properties"]
    assert entry["writes"] is True, "it can drop a table now"

    session = _session(tmp_path)
    try:
        out = entry["handler"](session, {})
        assert "pruned" not in out, "it does not drop anything unless told to"
    finally:
        session.close()


def test_no_stage_kind_vocabulary_is_left_in_the_drawing():
    """`weights` and `pnl` left the engine in 0.5.0; a palette keyed on them
    meant the starter project's own `normalized` stage drew in FAINT."""
    src = (Path(__file__).resolve().parents[1] / "src" / "qanat" / "graph.py").read_text()
    assert 'kind: str = "features"' not in src
    assert 'or "features"' not in src
    assert '"stage_kind") or "features"' not in src
