"""Projects in one home folder, from both doors (T-16).

The app and the MCP tools call the same `projects.py`, so a project made at one
door is there at the other; an MCP session works in the one project it selected;
and nobody deletes a project somebody is working in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qanat.mcp_http import create_app
from qanat.projects import (
    ProjectsError,
    Workspace,
    project_env,
    save_user_agent,
)

pytestmark = pytest.mark.xdist_group("projects")

HOST = {"host": "127.0.0.1"}


def client(home: Path, scope: str = "authoring", start_in: str | None = None) -> TestClient:
    ws = Workspace(home, doors=("mcp", "app"))
    app = create_app(None, scope, doors=("mcp", "app"), workspace=ws, start_in=start_in)
    return TestClient(app, base_url="http://127.0.0.1", headers=HOST)


def stamp(root: Path, when: str) -> None:
    """Set when a project was last used, exactly -- `projects.stamp` only moves it on."""
    (root / ".qanat" / "state.yaml").write_text(
        f"created_at: '2026-01-01T00:00:00Z'\nlast_updated: '{when}'\n")


def close(c: TestClient) -> None:
    c.app.state.qanat_workspace.close_all()


def session(c: TestClient) -> str:
    r = c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2024-11-05"}})
    return r.headers["mcp-session-id"]


def call(c: TestClient, sid: str, tool: str, **args) -> tuple[bool, object]:
    r = c.post("/mcp", headers={"mcp-session-id": sid},
               json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": tool, "arguments": args}}).json()["result"]
    text = r["content"][0]["text"]
    try:
        return r["isError"], json.loads(text)
    except json.JSONDecodeError:
        return r["isError"], text


# ---- starting with nothing ------------------------------------------------------
def test_the_app_starts_with_no_project_and_says_so(tmp_path: Path):
    c = client(tmp_path / "home")
    try:
        h = c.get("/api/health").json()
        assert h["project"] is None and h["manages_projects"] is True
        r = c.get("/api/tables")
        assert r.status_code == 409
        assert r.json() == {"detail": "There is no project to open.", "no_project": True}
    finally:
        close(c)


def test_the_app_opens_on_the_project_used_last(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("older")
    ws.create("newer")
    stamp(home / "older", "2026-01-01T00:00:00Z")
    stamp(home / "newer", "2026-06-01T00:00:00Z")
    c = client(home)
    try:
        assert c.get("/api/health").json()["project"] == "newer"
        assert c.get("/api/tables").status_code == 200
    finally:
        close(c)


# ---- one service, two doors -------------------------------------------------------
def test_a_project_made_at_one_door_is_there_at_the_other(tmp_path: Path):
    c = client(tmp_path / "home")
    try:
        assert c.post("/api/projects", json={"name": "from-page"}).status_code == 200
        sid = session(c)
        err, out = call(c, sid, "create_project", name="from-tool")
        assert not err, out
        err, listed = call(c, sid, "list_projects")
        assert {p["name"] for p in listed["projects"]} == {"from-page", "from-tool"}
        page = c.get("/api/projects").json()
        assert {p["name"] for p in page["projects"]} == {"from-page", "from-tool"}
    finally:
        close(c)


def test_the_order_is_last_updated_and_opening_or_closing_never_moves_it(tmp_path: Path):
    """It reordered on every open and close, which made the list impossible to scan."""
    home = tmp_path / "home"
    ws = Workspace(home)
    for n in ("a", "b", "c"):
        ws.create(n)
    stamp(home / "a", "2026-01-01T00:00:00Z")
    stamp(home / "b", "2026-02-01T00:00:00Z")
    stamp(home / "c", "2026-03-01T00:00:00Z")
    c = client(home)
    try:
        def order() -> list[str]:
            return [p["name"] for p in c.get("/api/projects").json()["projects"]]
        assert order() == ["c", "b", "a"]
        c.post("/api/projects/close")
        c.post("/api/projects/a/select")
        c.post("/api/projects/close")
        sid = session(c)
        call(c, sid, "select_project", name="a")
        call(c, sid, "close_project")
        call(c, sid, "select_project", name="b")
        call(c, sid, "close_project")
        assert order() == ["c", "b", "a"]
        _, listed = call(c, sid, "list_projects")
        assert [p["name"] for p in listed["projects"]] == ["c", "b", "a"]
        # a change does move it: a contact saved on `a`
        c.put("/api/projects/a/settings", json={"user_agent": "A a@example.com"})
        assert order()[0] == "a"
    finally:
        close(c)


def test_every_project_carries_when_it_was_created(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    made = ws.create("new")
    assert made["created_at"] and made["last_updated"] is None   # nothing done in it yet
    # an older project with no stamp still answers, from its file
    (home / "new" / ".qanat" / "state.yaml").write_text("last_used: '2026-05-01T00:00:00Z'\n")
    got = ws.describe("new")
    assert got["created_at"] and got["last_updated"] == "2026-05-01T00:00:00Z"


# ---- a session works in one project -------------------------------------------------
def test_before_a_project_is_selected_a_project_tool_refuses_and_names_them(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("semis")
    c = client(home)
    try:
        sid = session(c)
        err, said = call(c, sid, "list_tables")
        assert err and "Ask the user which project" in said and "semis" in said
    finally:
        close(c)


def test_two_sessions_work_in_two_projects(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("one")
    ws.create("two")
    c = client(home)
    try:
        a, b = session(c), session(c)
        call(c, a, "select_project", name="one")
        call(c, b, "select_project", name="two")
        _, ta = call(c, a, "list_tables")
        _, tb = call(c, b, "list_tables")
        assert ta["project"] == "one" and tb["project"] == "two"
        # switching mid-session: close first, as in the app
        err, said = call(c, a, "select_project", name="two")
        assert err and "Close 'one' first" in said
        _, still = call(c, a, "list_tables")
        assert still["project"] == "one"
        err, out = call(c, a, "close_project")
        assert not err and out["closed"] == "one"
        call(c, a, "select_project", name="two")
        _, again = call(c, a, "list_tables")
        assert again["project"] == "two"
    finally:
        close(c)


def test_started_inside_a_project_a_session_begins_there(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("here")
    c = client(home, start_in="here")
    try:
        _, out = call(c, session(c), "list_tables")
        assert out["project"] == "here"
    finally:
        close(c)


# ---- delete -------------------------------------------------------------------------
def test_delete_without_confirm_deletes_nothing_and_says_what_would_go(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("old")
    c = client(home)
    try:
        err, out = call(c, session(c), "delete_project", name="old")
        assert not err and out["deleted"] is False
        assert out["would_delete"]["files"] > 0
        assert (home / "old" / "qanat.yaml").is_file()
    finally:
        close(c)


def test_delete_with_confirm_removes_the_folder(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("old")
    c = client(home)
    try:
        err, out = call(c, session(c), "delete_project", name="old", confirm=True)
        assert not err and out["deleted"] is True
        assert not (home / "old").exists()
    finally:
        close(c)


def test_delete_refuses_the_sessions_own_project(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("mine")
    ws.create("other")
    c = client(home)
    try:
        sid = session(c)
        call(c, sid, "select_project", name="mine")
        err, said = call(c, sid, "delete_project", name="mine", confirm=True)
        assert err and "the one this session is working in" in said
        assert (home / "mine").is_dir()
    finally:
        close(c)


def test_delete_refuses_a_project_another_holder_is_working_in(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("busy")
    ws.create("free")
    c = client(home)
    try:
        c.post("/api/projects/busy/select")
        err, said = call(c, session(c), "delete_project", name="busy", confirm=True)
        assert err and "in use by the app" in said
        assert (home / "busy").is_dir()
    finally:
        close(c)


def test_delete_refuses_a_project_with_a_job_running(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("working")
    c = client(home)
    try:
        sid = session(c)
        call(c, sid, "select_project", name="working")
        monkeypatch.setattr(Workspace, "_running",
                            staticmethod(lambda o: [{"job": "filings", "kind": "ingestion",
                                                     "since": "now"}]))
        _, listed = call(c, sid, "list_projects")
        row = next(p for p in listed["projects"] if p["name"] == "working")
        assert row["status"] == "running" and row["running"][0]["job"] == "filings"
        page = next(p for p in c.get("/api/projects").json()["projects"]
                    if p["name"] == "working")
        assert page["status"] == "running"
        ws2 = c.app.state.qanat_workspace
        ws2.release(sid)
        ws2.release("app")      # the page opened it as the one used last
        with pytest.raises(ProjectsError, match="has a job running"):
            ws2.delete("working", "someone-else", confirm=True)
    finally:
        close(c)


def test_delete_refuses_a_project_another_program_holds(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("held")
    # The parent process is alive, so a note naming it reads as a live holder.
    (home / "held" / "data").mkdir(exist_ok=True)
    (home / "held" / "data" / ".qanat-lock").write_text(json.dumps(
        {"pid": os.getppid(), "serving": "http://127.0.0.1:9999", "doors": ["app"]}))
    c = client(home)
    try:
        sid = session(c)
        _, listed = call(c, sid, "list_projects")
        row = next(p for p in listed["projects"] if p["name"] == "held")
        assert row["status"] == "in use" and row["held_by"]["serving"].endswith(":9999")
        err, said = call(c, sid, "delete_project", name="held", confirm=True)
        assert err and "held by another program" in said
    finally:
        close(c)


def test_the_page_checks_the_typed_name_itself(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("keep")
    ws.create("other")
    c = client(home)
    try:
        c.post("/api/projects/other/select")
        assert c.delete("/api/projects/keep?confirm=kep").status_code == 400
        assert (home / "keep").is_dir()
        assert c.delete("/api/projects/keep?confirm=keep").status_code == 200
        assert not (home / "keep").exists()
    finally:
        close(c)


def test_a_closed_session_stops_holding_its_project(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("gone")
    ws.create("stay")
    c = client(home)
    try:
        sid = session(c)
        call(c, sid, "select_project", name="gone")
        c.request("DELETE", "/mcp", headers={"mcp-session-id": sid})
        err, out = call(c, session(c), "delete_project", name="gone", confirm=True)
        assert not err and out["deleted"] is True
    finally:
        close(c)


# ---- the scopes -----------------------------------------------------------------------
def test_each_project_tool_sits_at_its_scope(tmp_path: Path):
    from qanat import mcp

    at = {t["name"]: t["scope"] for t in mcp.TOOLS if t.get("home")}
    assert at == {"list_projects": "data", "select_project": "data", "close_project": "data",
                  "create_project": "full", "delete_project": "authoring"}
    c = client(tmp_path / "home", scope="research")
    try:
        assert c.post("/api/projects", json={"name": "x"}).status_code == 403
    finally:
        close(c)


def test_stdio_offers_no_project_tools_and_says_why(tmp_path: Path):
    from conftest import write_demo_project

    from qanat import mcp

    src = Path(mcp.__file__).read_text()
    assert 'tools = [t for t in offered if not t.get("home")]' in src
    write_demo_project(tmp_path, "demo")
    s = mcp.Session(str(tmp_path))
    try:
        stdio = [t for t in mcp.tools_for("authoring") if not t.get("home")]
        out = mcp._dispatch(s, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "list_projects", "arguments": {}}}, stdio)
        said = out["result"]["content"][0]["text"]
        assert "over stdio the folder" in said and "--scope" not in said
    finally:
        s.close()


# ---- the SEC contact and the theme ---------------------------------------------------
def test_a_projects_contact_is_what_the_sec_connector_sends(tmp_path: Path, monkeypatch):
    from qanat.connectors.sec import user_agent
    from qanat.models import IngestionJobDefinition

    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("a")
    ws.create("b")
    monkeypatch.setenv("SEC_USER_AGENT", "Shell shell@example.com")
    save_user_agent(home / "a", "Alpha alpha@example.com")
    job = IngestionJobDefinition(id="filings", to=["raw.documents"], connector="sec",
                                 options={"user_agent": "${SEC_USER_AGENT}"})
    assert user_agent(job, home / "a") == "Alpha alpha@example.com"
    assert user_agent(job, home / "b") == "Shell shell@example.com"
    literal = IngestionJobDefinition(id="filings", to=["raw.documents"], connector="sec",
                                     options={"user_agent": "Lit lit@example.com"})
    assert user_agent(literal, home / "a") == "Lit lit@example.com"
    assert project_env(home / "b") == {}


def test_a_contact_without_an_email_is_refused(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("a")
    with pytest.raises(ProjectsError, match="no email"):
        save_user_agent(home / "a", "just a name")


def test_the_contact_is_set_from_the_page_and_never_touches_the_project_files(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("a")
    before = {p: (home / "a" / p).read_bytes() for p in ("qanat.yaml", "jobs.yaml")}
    c = client(home)
    try:
        r = c.put("/api/projects/a/settings", json={"user_agent": "Jane jane@example.com"})
        assert r.status_code == 200
        assert c.get("/api/settings").json()["user_agent"]["user_agent"] == "Jane jane@example.com"
    finally:
        close(c)
    after = {p: (home / "a" / p).read_bytes() for p in ("qanat.yaml", "jobs.yaml")}
    assert before == after
    assert (home / "a" / ".qanat" / ".gitignore").read_text() == "*\n"


def test_the_theme_is_kept_after_a_restart(tmp_path: Path):
    home = tmp_path / "home"
    c = client(home)
    try:
        assert c.put("/api/settings/theme", json={"theme": "fidetolabs-dark"}).status_code == 200
        assert c.put("/api/settings/theme", json={"theme": "pink"}).status_code == 400
    finally:
        close(c)
    c = client(home)
    try:
        assert c.get("/api/health").json()["theme"] == "fidetolabs-dark"
    finally:
        close(c)


# ---- the command line -----------------------------------------------------------------
def test_the_home_is_always_the_named_one_or_qanat(tmp_path: Path, monkeypatch, capsys):
    """Never the folder that happens to hold a project: Qanat writes into its home."""
    from types import SimpleNamespace

    from qanat.cli import _home_and_start

    home = tmp_path / "home"
    Workspace(home).create("inside")
    elsewhere = tmp_path / "mixed"
    Workspace(elsewhere).create("stray")
    (elsewhere / "notes.txt").write_text("not qanat's")
    monkeypatch.delenv("QANAT_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    args = SimpleNamespace(home=None, project=None)

    monkeypatch.chdir(tmp_path)
    assert _home_and_start(args) == ((tmp_path / "user" / "qanat").resolve(), None)

    # inside a project outside the home: the home does not move, and it says so
    monkeypatch.chdir(elsewhere / "stray")
    got, start = _home_and_start(args)
    assert got == (tmp_path / "user" / "qanat").resolve() and start is None
    assert "is not in the home folder" in capsys.readouterr().out

    # inside a project in the home: that project opens
    monkeypatch.setenv("QANAT_HOME", str(home))
    monkeypatch.chdir(home / "inside")
    assert _home_and_start(args) == (home.resolve(), "inside")
    assert _home_and_start(SimpleNamespace(home=str(elsewhere), project=None)) == (
        elsewhere.resolve(), None)


def test_the_app_layer_still_holds_no_sql_after_settings():
    src = (Path(__file__).parent.parent / "src" / "qanat" / "app" / "api.py").read_text()
    for word in ("SELECT ", "INSERT ", "DELETE FROM", "shutil", "rmtree"):
        assert word not in src


def test_the_pages_are_never_served_from_a_stale_cache(tmp_path: Path):
    """A browser kept an old `app.js` and ran the old page against the new API."""
    c = client(tmp_path / "home")
    try:
        for path in ("/", "/static/app.js", "/static/app.css"):
            assert c.get(path).headers["cache-control"] == "no-cache", path
    finally:
        close(c)


# ---- one open at a time in the app ----------------------------------------------------
def test_close_lets_the_project_go_and_its_lock_with_it(tmp_path: Path):
    from qanat.project import load
    from qanat.store import Store

    home = tmp_path / "home"
    Workspace(home).create("mine")
    c = client(home)
    try:
        assert c.get("/api/health").json()["project"] == "mine"    # opened as the one used last
        r = c.post("/api/projects/close").json()
        assert r == {"closed": "mine", "released": True}
        # nobody holds it now: another program takes the writer lock
        project, root = load(home / "mine")
        other = Store(project.store_url(root))
        try:
            other.take_writer_lock(serving="test")
        finally:
            other.close()
        # and the app does not jump back into it, or into anything else
        assert c.get("/api/health").json()["project"] is None
        assert c.get("/api/tables").status_code == 409
    finally:
        close(c)


def test_a_second_project_cannot_be_opened_until_the_first_is_closed(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("first")
    ws.create("second")
    c = client(home)
    try:
        c.post("/api/projects/first/select")
        r = c.post("/api/projects/second/select")
        assert r.status_code == 400 and "Close 'first' first" in r.json()["detail"]
        c.post("/api/projects/close")
        assert c.post("/api/projects/second/select").status_code == 200
        assert c.get("/api/health").json()["project"] == "second"
    finally:
        close(c)


def test_close_keeps_the_project_open_for_a_session_still_in_it(tmp_path: Path):
    home = tmp_path / "home"
    Workspace(home).create("shared")
    c = client(home)
    try:
        c.post("/api/projects/shared/select")
        sid = session(c)
        call(c, sid, "select_project", name="shared")
        assert c.post("/api/projects/close").json() == {"closed": "shared", "released": False}
        _, out = call(c, sid, "list_tables")
        assert out["project"] == "shared"
    finally:
        close(c)


def test_close_is_refused_while_a_job_runs(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    Workspace(home).create("busy")
    c = client(home)
    try:
        c.post("/api/projects/busy/select")
        monkeypatch.setattr(Workspace, "_running",
                            staticmethod(lambda o: [{"job": "parse_facts"}]))
        r = c.post("/api/projects/close")
        assert r.status_code == 400 and "parse_facts" in r.json()["detail"]
        assert c.get("/api/health").json()["project"] == "busy"
    finally:
        close(c)


def test_each_project_lists_its_own_sec_contact_on_both_doors(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Shell shell@example.com")
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("set")
    ws.create("unset")
    save_user_agent(home / "set", "Jane jane@example.com")
    c = client(home)
    try:
        page = c.get("/api/projects").json()
        got = {p["name"]: p["sec_contact"] for p in page["projects"]}
        assert got == {"set": "Jane jane@example.com", "unset": None}
        assert page["shell_sec_contact"] == "Shell shell@example.com"
        _, listed = call(c, session(c), "list_projects")
        assert {p["name"]: p["sec_contact"] for p in listed["projects"]} == got
    finally:
        close(c)


def test_the_page_loads_its_assets_by_a_stamped_address(tmp_path: Path):
    """A changed file gets a new address, so no browser cache can hold the old one."""
    import re

    c = client(tmp_path / "home")
    try:
        html = c.get("/").text
        css = re.search(r'/static/app\.css\?v=([0-9a-f]{12})"', html)
        js = re.search(r'/static/app\.js\?v=([0-9a-f]{12})"', html)
        assert css and js, html
        assert c.get(f"/static/app.css?v={css.group(1)}").status_code == 200
    finally:
        close(c)


def test_a_stamp_older_than_the_old_word_does_not_win(tmp_path: Path):
    """A state file from before `last_updated` kept `last_used`; the first stamp
    after the rename must not replace a newer time with an older job's."""
    from qanat.projects import last_updated
    from qanat.projects import stamp as real_stamp

    home = tmp_path / "home"
    Workspace(home).create("old")
    (home / "old" / ".qanat" / "state.yaml").write_text("last_used: '2026-11-09T07:00:00Z'\n")
    real_stamp(home / "old", "2026-11-09T02:00:00Z")
    assert last_updated(home / "old") == "2026-11-09T07:00:00Z"


def test_a_running_server_serves_the_page_it_started_with(tmp_path: Path, monkeypatch):
    """Read per request, an old server handed out a new page against its old API."""
    from qanat.app import api

    c = client(tmp_path / "home")
    try:
        before = c.get("/static/app.js").content
        monkeypatch.setattr(api, "STATIC", tmp_path / "nowhere")   # the disk changes under it
        assert c.get("/static/app.js").content == before
        assert "/static/app.js?v=" in c.get("/").text
    finally:
        close(c)


def test_a_project_made_by_init_has_a_created_date_and_no_update(tmp_path: Path):
    """`qanat init` in a shell stamps nothing; the list still knows when it was made."""
    from qanat.scaffold import write_project

    home = tmp_path / "home"
    (home / "by-hand").mkdir(parents=True)
    write_project(home / "by-hand", "by_hand")
    got = Workspace(home).describe("by-hand")
    assert got["created_at"] and got["last_updated"] is None


def test_a_project_never_updated_sorts_by_when_it_was_made(tmp_path: Path):
    home = tmp_path / "home"
    ws = Workspace(home)
    ws.create("old")
    stamp(home / "old", "2020-01-01T00:00:00Z")     # updated long ago
    ws.create("fresh")                               # created now, never updated
    assert [p["name"] for p in ws.list()["projects"]] == ["fresh", "old"]
    assert ws.describe("fresh")["last_updated"] is None
