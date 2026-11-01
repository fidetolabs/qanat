"""The surface is not the data model.

**T-01 took the model from two objects to eight.** These are the tests that the
surface did not follow it there. A mirror of it would be about thirty-two tools
-- four objects, two kinds, create-read-update-delete -- generated from the shape
of the schema rather than from anything a caller needs.

`docs/flows.md` is where every tool is traced to something a caller does. A tool
that is not in it has no flow behind it, which is the only reason a tool is on
this surface at all.
"""

from __future__ import annotations

import re
from pathlib import Path

from qanat import mcp

FLOWS = Path(__file__).parent.parent / "docs" / "flows.md"

#: What the model is named with, and what the surface may not be. The entities
#: get the real names; the tools are named for what a caller does.
ENTITY_WORDS = ("job", "definition", "schedule", "execution")

#: The surface measured on `qanat 0.10.2`, which is the release this plan opened
#: against. **The model gained six objects and these did not move.** A number
#: here going up is the test failing, whatever the reason.
BUDGET = {"data": 14, "research": 20, "full": 28, "authoring": 33}


def test_no_tool_is_named_after_an_entity():
    bad = {t["name"]: [w for w in ENTITY_WORDS if w in t["name"]] for t in mcp.TOOLS}
    bad = {k: v for k, v in bad.items() if v}
    assert not bad, f"named for the object rather than the act: {bad}"


def test_the_count_at_each_scope_has_not_grown():
    got = {scope: len(mcp.tools_for(scope)) for scope in mcp.SCOPES}
    over = {s: (got[s], BUDGET[s]) for s in got if got[s] > BUDGET[s]}
    assert not over, f"the surface grew: {over}"


def test_every_tool_traces_to_a_written_flow():
    """A tool with no flow behind it is removed before the release."""
    said = FLOWS.read_text()
    missing = [t["name"] for t in mcp.TOOLS if f"`{t['name']}`" not in said]
    assert not missing, f"no flow written for: {missing}"


def test_every_flow_names_a_tool_that_exists():
    """The other direction: a flow pointing at a tool that is not there is a
    document describing a surface somebody cannot call."""
    said = FLOWS.read_text()
    # the candidates section names tools on purpose, as the ones that did not ship
    kept, _, _ = said.partition("## The candidates that did not ship")
    named = set(re.findall(r"`([a-z][a-z_]+)`", kept))
    have = {t["name"] for t in mcp.TOOLS}
    # words in backticks that look like tool names but are fields or tables
    not_tools = {
        "ingestion_job", "job_execution", "ingestion_job_definition",
        "transform_job_definition", "job_schedule", "ingestion_job_schedule",
        "data", "research", "full", "authoring", "universe", "me", "save",
        "define",
    }
    stray = sorted(n for n in named - have - not_tools if "_" in n)
    assert not stray, f"the flows name tools that do not exist: {stray}"


def test_the_renamed_tools_are_refused_by_name_and_not_aliased():
    """An alias leaves both vocabularies on the surface, which is the state the
    rename exists to end."""
    have = {t["name"] for t in mcp.TOOLS}
    for old, new in mcp.MOVED.items():
        assert old not in have, f"{old} still answers"
        assert new in have, f"{old} was renamed to {new}, which does not exist"


def test_calling_a_renamed_tool_says_what_it_is_called_now(tmp_path: Path):
    from conftest import write_demo_project as write_project

    write_project(tmp_path, "demo")
    session = mcp.Session(str(tmp_path))
    try:
        for old, new in mcp.MOVED.items():
            out = mcp._dispatch(
                session,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": old, "arguments": {}}},
                mcp.tools_for("authoring"))
            said = out["result"]["content"][0]["text"]
            assert out["result"].get("isError"), old
            assert f"'{new}'" in said, f"{old}: {said}"
    finally:
        session.close()


def test_the_entity_words_are_all_over_the_model_and_nowhere_on_the_surface():
    """Both halves of the claim, because only one of them is interesting."""
    from qanat.models import Project

    fields = set(Project.model_fields)
    assert {"ingestion_job_definitions", "transform_job_definitions",
            "job_schedules"} <= fields, "the model has the real names"

    from qanat.store import EXECUTIONS, JOBS, VERSIONS

    for table in (JOBS, EXECUTIONS, VERSIONS):
        assert any(w in table for w in ENTITY_WORDS), table


def test_the_tool_entry_rule_is_written_down_with_the_new_clause():
    # Wrapped prose, so the sentences are matched against one line of it.
    said = " ".join(FLOWS.read_text().split()).lower()
    assert "an entity is not a reason for a tool" in said
    assert "every tool definition is sent on every request" in said
    assert "a door is not a flow" in said


def test_no_tool_exists_because_one_door_needs_it():
    """Every flow is written once, and it is the same flow whether the caller is
    a door ① agent or hosted Margiana."""
    said = " ".join(FLOWS.read_text().split()).lower()
    assert "door ①" in said and "door ②" in said
    assert "authors jobs by hand" in said, "the flow that is not one is named"


def test_every_tool_can_actually_be_reached(tmp_path: Path):
    """**Called with nothing, and the only acceptable complaint is the arguments.**

    A tool whose handler cannot even be entered -- a stale import, a renamed
    function it still calls by the old name -- answers with `ImportError` or
    `NameError` rather than with a message about what it needs. Nothing else in
    the suite catches that, because a test only covers the tools it happens to
    call: `define_universe` shipped importing `define_universe` from the editor,
    which is named `save_universe` there, and every test passed.
    """
    from conftest import write_demo_project as write_project

    write_project(tmp_path, "demo")
    session = mcp.Session(str(tmp_path))
    broken = {}
    try:
        for entry in mcp.tools_for("authoring"):
            out = mcp._dispatch(
                session,
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                 "params": {"name": entry["name"], "arguments": {}}},
                mcp.tools_for("authoring"))
            said = out["result"]["content"][0]["text"]
            if said.startswith(("ImportError", "NameError", "AttributeError",
                                "ModuleNotFoundError")):
                broken[entry["name"]] = said.splitlines()[0]
    finally:
        session.close()
    assert not broken, broken
