"""The console refuses requests that asked for a name it does not answer to.

The API writes step scripts and runs them, so reaching it is as good as running
code on the machine. Binding to loopback does not prevent that on its own: a page
the person visits can resolve its own domain to 127.0.0.1 and call the console
same-origin, which is what these tests are about.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qanat.api import AppState, _hostname, allowed_hosts, create_app
from qanat.project import load
from qanat.scaffold import write_project
from qanat.store import Store


def _app(tmp_path: Path, **kw):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    state = AppState(store=store, project=project, root=root, sched=None)
    return create_app(state, **kw)


@pytest.fixture
def client(tmp_path: Path):
    return TestClient(_app(tmp_path), base_url="http://127.0.0.1:8420")


# ----------------------------------------------------------------- the parser

@pytest.mark.parametrize("raw,want", [
    ("127.0.0.1", "127.0.0.1"),
    ("127.0.0.1:8420", "127.0.0.1"),
    ("localhost:8420", "localhost"),
    ("http://localhost:8420", "localhost"),
    ("https://evil.example.com", "evil.example.com"),
    ("http://evil.example.com/api/steps", "evil.example.com"),
    ("[::1]:8420", "::1"),
    ("[::1]", "::1"),
    ("", ""),
])
def test_hostname_strips_scheme_and_port(raw, want):
    assert _hostname(raw) == want


def test_a_port_is_not_mistaken_for_an_ipv6_host():
    # rpartition on ":" would eat the last group of an unbracketed v6 address
    assert _hostname("host:notaport") == "host:notaport"


# ------------------------------------------------------------- what is allowed

def test_loopback_is_allowed_by_default():
    names = allowed_hosts()
    assert {"localhost", "127.0.0.1", "::1"} <= names
    assert "evil.example.com" not in names


def test_env_var_adds_names(monkeypatch):
    monkeypatch.setenv("QANAT_ALLOWED_HOSTS", "qanat.example.com, Box.Local ")
    names = allowed_hosts()
    assert "qanat.example.com" in names
    assert "box.local" in names          # lowercased
    assert "localhost" in names          # loopback survives


def test_env_var_ignores_blanks(monkeypatch):
    monkeypatch.setenv("QANAT_ALLOWED_HOSTS", " , ,")
    assert allowed_hosts() == {"localhost", "127.0.0.1", "::1"}


# ------------------------------------------------------------------ the guard

def test_loopback_still_works(client):
    assert client.get("/api/project").status_code == 200


def test_a_rebound_domain_is_refused(client):
    """The rebinding case: resolves to 127.0.0.1, but asks for another name."""
    r = client.get("/api/project", headers={"Host": "evil.example.com"})
    assert r.status_code == 403
    assert "evil.example.com" in r.json()["detail"]


def test_a_rebound_domain_cannot_write_a_step(client):
    """The one that matters: POST /api/steps writes a script to disk."""
    r = client.post(
        "/api/steps",
        headers={"Host": "evil.example.com"},
        json={"id": "pwn", "from": ["raw.daily_prices"], "to": ["features.pwn"],
              "script": "steps/pwn.py"},
    )
    assert r.status_code == 403


def test_a_rebound_domain_cannot_run_a_job(client):
    r = client.post("/api/jobs/normalize/run", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_a_foreign_origin_is_refused(client):
    """Host is fine, but the page driving it is not."""
    r = client.post(
        "/api/steps",
        headers={"Host": "127.0.0.1:8420", "Origin": "https://evil.example.com"},
        json={"id": "pwn", "from": [], "to": [], "script": "steps/pwn.py"},
    )
    assert r.status_code == 403
    assert "evil.example.com" in r.json()["detail"]


def test_a_null_origin_is_refused(client):
    """What a sandboxed frame or a file:// page sends."""
    r = client.get("/api/project", headers={"Origin": "null"})
    assert r.status_code == 403


def test_the_consoles_own_origin_is_accepted(client):
    r = client.get("/api/project", headers={"Origin": "http://127.0.0.1:8420"})
    assert r.status_code == 200


def test_no_host_header_is_refused(tmp_path: Path):
    app = _app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8420") as c:
        r = c.get("/api/project", headers={"Host": ""})
        assert r.status_code == 403


# --------------------------------------------------------------- serving wider

def test_a_named_host_can_be_allowed_explicitly(tmp_path: Path):
    app = _app(tmp_path, allow_hosts=["qanat.example.com"])
    c = TestClient(app, base_url="http://qanat.example.com")
    assert c.get("/api/project").status_code == 200
    # and everything else is still refused
    assert c.get("/api/project", headers={"Host": "evil.example.com"}).status_code == 403


def test_the_env_var_reaches_a_built_app(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QANAT_ALLOWED_HOSTS", "box.local")
    c = TestClient(_app(tmp_path), base_url="http://box.local")
    assert c.get("/api/project").status_code == 200


def test_the_console_page_is_guarded_too(client):
    """Not just /api: the page itself carries the scripts that call it."""
    assert client.get("/", headers={"Host": "evil.example.com"}).status_code == 403
