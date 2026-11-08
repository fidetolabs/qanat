"""The surface is not the data model.

**The model went from two objects to eight.** These are the tests that the
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

def test_no_tool_is_named_after_an_entity():
    """An entity word is allowed to enumerate, never to act.

    A9 banned the four words outright, and that was drawn wider than its own
    reason. The rule exists so the surface does not grow one tool per entity --
    and a `list_` tool does not grow it: it is one tool over the whole set, and
    the exact word is the only one a caller can guess. `pipeline` and `history`
    were the alternatives, and neither says what it returns. A tool that *acts*
    on an entity still may not name it, which is what this now holds.
    """
    bad = {}
    for t in mcp.TOOLS:
        if t["name"].startswith("list_"):
            continue
        hits = [w for w in ENTITY_WORDS if w in t["name"]]
        if hits:
            bad[t["name"]] = hits
    assert not bad, f"named for the object rather than the act: {bad}"


def test_an_entity_word_still_cannot_name_a_tool_that_acts():
    """The half of A9 that did not move."""
    for name in ("define_job", "save_schedule", "run_execution", "remove_definition"):
        assert name not in {t["name"] for t in mcp.TOOLS}


def test_the_scopes_nest_and_none_of_them_is_empty():
    """What replaced the tool-count ceiling.

    A fixed number per scope was measured on 0.10.2 and then read as a rule. It
    was a proxy for the thing actually worth holding -- that a tool exists for a
    flow and not for an object -- and two other tests in this file hold that
    directly. Once every scope sat exactly on its number the ceiling stopped
    being a ratchet and only blocked the next honest addition, so it is gone.

    What is worth asserting is the shape: each scope contains the one before it,
    so a server started at `research` offers the `data` tools too, and the
    nesting is real rather than four hand-maintained lists.
    """
    seen: set[str] = set()
    for scope in mcp.SCOPES:
        here = {t["name"] for t in mcp.tools_for(scope)}
        assert here, f"{scope} offers no tools"
        assert seen <= here, f"{scope} dropped a tool a narrower scope offered"
        seen = here


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
        "define", "from_universe", "time_columns", "job_timeout",
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


def test_no_tool_declares_an_argument_its_handler_ignores():
    """**A declared argument nobody reads is a promise the surface does not keep**
    -- and every tool definition is sent on every request, so it costs tokens on
    every call to say nothing.

    The other direction is worse: an argument a handler reads and the schema does
    not advertise is a quiet alias, which is what `source` and `step` were
    retired for. `subscribe_filings` read `forms` *or* `form`, and only the
    plural was in the schema, so a caller using the singular worked while every
    document said otherwise.
    """
    import inspect
    import re as _re

    mismatched = {}
    for entry in mcp.TOOLS:
        src = inspect.getsource(entry["handler"])
        seen = set(_re.findall(r'args\.get\(\s*["\']([a-z_]+)["\']', src))
        seen |= set(_re.findall(r'args\[\s*["\']([a-z_]+)["\']\s*\]', src))
        seen |= set(_re.findall(r'_need\(\s*args\s*,\s*["\']([a-z_]+)["\']', src))
        seen |= set(_re.findall(r'args\.pop\(\s*["\']([a-z_]+)["\']', src))
        if "args.items()" in src:
            continue            # passes the whole dict through, by design
        declared = set(entry["inputSchema"]["properties"])
        unread, undeclared = declared - seen, seen - declared
        if unread or undeclared:
            mismatched[entry["name"]] = {"declared but unread": sorted(unread),
                                         "read but undeclared": sorted(undeclared)}
    assert not mismatched, mismatched


def test_a_universe_is_a_csv_and_nothing_in_the_project_names_one():
    """It used to be both a file and a `universes:` key. The key was read by
    `ctx.universe()` inside a transform -- a basket held by one stage of the
    pipeline, which is the shape `weights` and `pnl` had, and they left in 0.5.0.
    """
    from qanat.context import Context
    from qanat.models import Project, TransformJobDefinition

    assert "universes" not in Project.model_fields
    assert "universe" not in TransformJobDefinition.model_fields
    assert not hasattr(Context, "universe"), "ctx.universe() was the only reader"

    from qanat import models

    assert not hasattr(models, "Universe"), "one class of that name, not two"

    # And the half that is live: a connector resolves the csv by name.
    from qanat.connectors.sec import universe_path

    assert callable(universe_path)


def test_a_screen_takes_clauses_or_a_filter_and_never_both():
    """A saved filter is named at the top of the call; a clause is never a wrapper.

    `screen` once accepted `[{filter, version, clauses}]` inside the clause list,
    and the wrapper did nothing: every clause was AND-ed into one verdict and the
    name came back unread. A filter the operator saves is named with `filter`
    instead, and the nested shape stays refused.
    """
    import pytest as _pytest

    from qanat import screen as screen_mod

    entry = next(t for t in mcp.TOOLS if t["name"] == "screen_companies")
    props = entry["inputSchema"]["properties"]
    assert {"clauses", "filter"} <= set(props)
    assert entry["inputSchema"]["required"] == [], "one of the two, checked in run()"
    assert "filters" not in props
    assert not hasattr(screen_mod, "Filter"), "the wrapper is gone, not deprecated"

    with _pytest.raises(screen_mod.ScreenError, match="not a named filter"):
        screen_mod.parse_clauses(
            [{"filter": "mine", "version": 1,
              "clauses": [{"metric": "pe", "op": "lt", "value": 10}]}])


def test_applied_echoes_what_the_caller_could_not_have_known():
    """And nothing else. The version a clause read is the server's answer; the
    clause itself is the caller's own words handed back so a kept record reads
    without a second call."""
    from qanat.screen import Clause

    bare = Clause(metric="pe", op="lt", value=10).as_applied()
    assert bare == {"metric": "pe", "op": "lt", "value": 10.0}
    assert "filter" not in bare and "version" not in bare

    ran = Clause(metric="pe", op="lt", value=10).as_applied({"pe": 3})
    assert ran["metric_version_used"] == 3
