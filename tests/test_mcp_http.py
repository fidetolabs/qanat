"""The hosted door. Same tools, same scopes, a transport that crosses a network."""

from pathlib import Path

import pytest
from conftest import write_demo_project as write_project
from fastapi.testclient import TestClient

from qanat import mcp
from qanat.mcp_http import Sessions, create_app
from qanat.runner import run_all


def _client(tmp_path: Path, scope: str = "research", token: str | None = None) -> TestClient:
    write_project(tmp_path, "demo")
    app = create_app(str(tmp_path), scope, token=token)
    session = app.state.qanat_session
    assert all(r.ok for r in run_all(session.store, session.project, session.root))
    # TestClient sends `Host: testserver`, which the guard is right to refuse.
    return TestClient(app, headers={"Host": "127.0.0.1"})


def _rpc(client: TestClient, method: str, params=None, sid: str | None = None, mid: int = 1):
    headers = {"Mcp-Session-Id": sid} if sid else {}
    body = {"jsonrpc": "2.0", "id": mid, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers)


def _handshake(client: TestClient) -> str:
    res = _rpc(client, "initialize", {"protocolVersion": mcp.PROTOCOL})
    assert res.status_code == 200
    sid = res.headers["Mcp-Session-Id"]
    assert sid
    return sid


# ------------------------------------------------------------------- handshake
def test_initialize_answers_and_opens_a_session(tmp_path: Path):
    client = _client(tmp_path)
    res = _rpc(client, "initialize", {"protocolVersion": mcp.PROTOCOL})
    assert res.json()["result"]["serverInfo"]["name"] == "qanat"
    assert res.headers["Mcp-Session-Id"]


def test_a_call_without_a_session_is_refused(tmp_path: Path):
    client = _client(tmp_path)
    res = _rpc(client, "tools/list")
    assert res.status_code == 400
    assert "initialize" in res.json()["error"]["message"]


def test_an_unknown_session_is_told_to_start_again(tmp_path: Path):
    client = _client(tmp_path)
    _handshake(client)
    res = _rpc(client, "tools/list", sid="not-a-session")
    assert res.status_code == 404
    assert "initialize" in res.json()["error"]["message"]


def test_a_notification_is_accepted_with_no_body(tmp_path: Path):
    client = _client(tmp_path)
    sid = _handshake(client)
    res = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid, "Host": "127.0.0.1"},
    )
    assert res.status_code == 202
    assert not res.content


def test_delete_ends_the_session(tmp_path: Path):
    client = _client(tmp_path)
    sid = _handshake(client)
    assert client.delete("/mcp", headers={"Mcp-Session-Id": sid}).status_code == 204
    assert _rpc(client, "tools/list", sid=sid).status_code == 404


# ----------------------------------------------------------------------- scope
def test_the_scope_is_the_servers_and_not_the_callers(tmp_path: Path):
    client = _client(tmp_path, scope="data")
    sid = _handshake(client)

    listed = _rpc(client, "tools/list", sid=sid).json()["result"]["tools"]
    assert {t["name"] for t in listed} == {t["name"] for t in mcp.tools_for("data")}

    # Nothing a caller can put in a request widens it.
    res = _rpc(client, "tools/call",
               {"name": "screen_companies", "arguments": {}, "scope": "research"}, sid=sid)
    body = res.json()["result"]["content"][0]["text"]
    assert res.json()["result"]["isError"] is True
    assert "'research' scope" in body


def test_a_tool_in_scope_answers_with_real_rows(tmp_path: Path):
    client = _client(tmp_path)
    sid = _handshake(client)
    res = _rpc(client, "tools/call", {"name": "list_tables", "arguments": {}}, sid=sid)
    assert res.json()["result"]["isError"] is False
    assert "tables" in res.json()["result"]["content"][0]["text"]


def test_a_bad_scope_never_gets_a_port(tmp_path: Path):
    write_project(tmp_path, "demo")
    with pytest.raises(mcp.ScopeError):
        create_app(str(tmp_path), "readonly")


# ------------------------------------------------------------------ the guards
def test_a_host_we_do_not_answer_to_is_refused(tmp_path: Path):
    client = _client(tmp_path)
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                      headers={"Host": "evil.example.com"})
    assert res.status_code == 403


def test_a_token_is_required_when_one_is_set(tmp_path: Path):
    client = _client(tmp_path, token="s3cret")
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                      headers={"Host": "127.0.0.1"})
    assert res.status_code == 401

    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                      headers={"Host": "127.0.0.1", "Authorization": "Bearer s3cret"})
    assert res.status_code == 200


def test_the_wrong_token_is_refused(tmp_path: Path):
    client = _client(tmp_path, token="s3cret")
    res = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                      headers={"Host": "127.0.0.1", "Authorization": "Bearer guess"})
    assert res.status_code == 401


def test_get_says_there_is_nothing_to_stream(tmp_path: Path):
    client = _client(tmp_path)
    assert client.get("/mcp").status_code == 405


def test_health_reports_the_scope_it_is_serving(tmp_path: Path):
    client = _client(tmp_path, scope="data")
    body = client.get("/health").json()
    assert body["scope"] == "data"
    assert body["tools"] == len(mcp.tools_for("data"))
    assert body["of"] == len(mcp.TOOLS)


# -------------------------------------------------------------------- sessions
def test_sessions_forget_the_oldest_rather_than_growing_forever():
    from qanat.mcp_http import MAX_SESSIONS

    s = Sessions()
    first = s.open()
    for _ in range(MAX_SESSIONS):
        s.open()
    assert len(s) <= MAX_SESSIONS
    assert not s.touch(first)


def test_a_batch_is_answered_as_a_batch(tmp_path: Path):
    client = _client(tmp_path)
    sid = _handshake(client)
    res = client.post(
        "/mcp",
        json=[
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ],
        headers={"Mcp-Session-Id": sid, "Host": "127.0.0.1"},
    )
    body = res.json()
    assert isinstance(body, list) and len(body) == 2
    assert {m["id"] for m in body} == {1, 2}


# ---------------------------------------------------------------- the doors
#
# `qanat serve --mcp --app` is one program with two doors, and either one may be
# left off. These hold the half of that which is not "does the page load": that a
# door nobody asked for is **not on the port**.


def _doors(tmp_path: Path, doors) -> TestClient:
    write_project(tmp_path, "demo")
    app = create_app(str(tmp_path), "research", doors=doors)
    return TestClient(app, headers={"Host": "127.0.0.1"})


def test_the_app_door_alone_puts_nothing_at_mcp(tmp_path: Path):
    """A person who wanted a browser did not ask for a tool endpoint on their
    port. `--app` used to be a flag on `qanat mcp`, where there was no way to
    take `/mcp` off at all."""
    c = _doors(tmp_path, ("app",))
    try:
        assert c.get("/").status_code == 200
        assert c.get("/api/health").status_code == 200
        assert c.get("/mcp").status_code == 404
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 404
    finally:
        c.app.state.qanat_session.close()


def test_the_mcp_door_alone_puts_nothing_at_the_pages(tmp_path: Path):
    c = _doors(tmp_path, ("mcp",))
    try:
        assert c.get("/mcp").status_code == 405      # the spec's answer, not a 404
        assert c.get("/").status_code == 404
        assert c.get("/api/health").status_code == 404
    finally:
        c.app.state.qanat_session.close()


def test_both_doors_do_not_care_which_order_they_were_named(tmp_path: Path):
    """One arrangement, one spelling: `--mcp --app` and `--app --mcp` are the
    same server, so neither can mean something the other does not."""
    for order in (("mcp", "app"), ("app", "mcp")):
        c = _doors(tmp_path, order)
        try:
            assert c.get("/").status_code == 200
            assert c.get("/mcp").status_code == 405
            assert c.app.state.doors == order
        finally:
            c.app.state.qanat_session.close()


def test_a_server_with_no_door_is_refused(tmp_path: Path):
    """Serving nothing is not a default. The CLI says this in words; here it is
    a programming error, because nothing but the CLI builds one."""
    write_project(tmp_path, "demo")
    with pytest.raises(ValueError, match="at least one door"):
        create_app(str(tmp_path), "research", doors=())


# ------------------------------------------------------- the verb, not the flag
#
# `--http` changed what the program was: over stdio this process is the client's
# child with no port, and with `--http` it was an HTTP server. Two programs, so
# two verbs -- and the flag has to say where it went rather than die in argparse.


def test_mcp_refuses_the_flags_that_became_a_verb():
    from qanat.cli import main

    assert main(["mcp", "--http"]) == 2
    assert main(["mcp", "--http", "--schedule"]) == 2
    assert main(["mcp", "--schedule"]) == 2


def test_serve_with_no_door_names_both(capsys):
    from qanat.cli import main

    assert main(["serve"]) == 2
    said = capsys.readouterr().err
    assert "--mcp" in said and "--app" in said


def test_serve_is_not_a_removed_command_any_more():
    """It went in 0.10.0 for forcing the clock on, and the clock is a flag here.
    So it is a verb again, and `MOVED` must not still be answering for it."""
    from qanat.cli import MOVED

    assert "serve" not in MOVED
