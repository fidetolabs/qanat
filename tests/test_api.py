"""Console API for editing the pipeline."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qanat.api import AppState, create_app
from qanat.project import load
from qanat.runner import run_all
from qanat.scaffold import write_project
from qanat.store import Store


@pytest.fixture
def client(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    state = AppState(store=store, project=project, root=root, sched=None)
    return TestClient(create_app(state), base_url="http://127.0.0.1:8420"), state


def test_get_project(client):
    c, _ = client
    r = c.get("/api/project")
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert body["config"]["project"] == "demo"


def test_add_stage(client):
    c, state = client
    r = c.post("/api/stages", json={
        "id": "enriched",
        "kind": "features",
        "description": "extra layer",
        "before": "weights",
    })
    assert r.status_code == 200
    state.reload()
    ids = [s.id for s in state.project.stages]
    assert "enriched" in ids
    assert ids.index("enriched") < ids.index("weights")


def test_add_source(client):
    c, state = client
    r = c.post("/api/sources", json={
        "id": "extra",
        "to": ["raw.extra_bars"],
        "connector": "synthetic",
        "schedule": "*/10 * * * *",
        "mode": "replace",
        "options": {"series": "bars", "from_universe": "./universes/demo8.csv"},
    })
    assert r.status_code == 200
    state.reload()
    assert any(s.id == "extra" for s in state.project.sources)


def test_add_step_creates_script(client, tmp_path: Path):
    c, state = client
    r = c.post("/api/steps", json={
        "id": "extra_step",
        "from": ["normalized.prices"],
        "to": ["features.extra"],
        "script": "steps/extra.sql",
        "schedule": "*/10 * * * *",
    })
    assert r.status_code == 200
    assert (tmp_path / "steps" / "extra.sql").is_file()
    state.reload()
    assert any(s.id == "extra_step" for s in state.project.steps)


def test_retention_api(client):
    c, state = client
    r = c.put("/api/retention", json={"retention": {"raw.daily_prices": "7d"}})
    assert r.status_code == 200
    state.reload()
    assert state.project.retention["raw.daily_prices"] == "7d"


def test_prune_orphan(client, tmp_path: Path):
    c, state = client
    run_all(state.store, state.project, state.root)
    yaml = (tmp_path / "qanat.yaml").read_text()
    yaml = yaml.replace("  - id: tone\n", "")
    yaml = yaml.replace("features.tone", "features.momentum")
    yaml = yaml.replace("from: [features.momentum, features.risk, features.tone]",
                        "from: [features.momentum, features.risk]")
    (tmp_path / "qanat.yaml").write_text(yaml)
    state.reload()
    r = c.post("/api/prune")
    assert r.status_code == 200
    assert any(d["ref"] == "features.tone" for d in r.json()["dropped"])


def test_agent_choice_refuses_a_cli_we_cannot_drive(client):
    """Better to refuse at the door than to write it and fail at the ask box."""
    c, state = client
    r = c.put("/api/agent", json={"cli": "not-an-agent"})
    assert r.status_code >= 400
    state.reload()
    assert state.project.agent is None or state.project.agent.cli == ""


# ---------------------------------------------------------------- sessions
def _seed_session(state, sid="s-1", alpha="alpha_momentum", runs=1):
    state.store.open_session(sid, cli="Claude Code", title="add a momentum strategy")
    state.store.note_ask(sid, cost_usd=0.11, model="claude-opus-5")
    ids = []
    for i in range(runs):
        rid = state.store.start_backtest("2026-01-01", "2026-06-01", "5d", i, f"dig{i}",
                                         alpha=alpha, session_id=sid)
        state.store.end_backtest(rid, "ok", {"net": 0.1 + i / 100, "gross": 0.1, "fees": 0.0,
                                             "slippage": 0.0, "turnover": 1.0, "periods": 10})
        ids.append(rid)
    return ids


def test_a_cli_or_scheduler_replay_belongs_to_no_session(client):
    """Tagging one would invent a conversation nobody had."""
    _, state = client
    rid = state.store.start_backtest("2026-01-01", "2026-06-01", "5d", 0, "d", alpha="a")
    state.store.end_backtest(rid, "ok", {"net": 0.0, "gross": 0.0, "fees": 0.0,
                                         "slippage": 0.0, "turnover": 0.0, "periods": 1})
    row = next(r for r in state.store.backtests() if r["run_id"] == rid)
    assert not row["session_id"]


# ---------------------------------------------------------------- the ledger
def _ok_run(state, digest, alpha="alpha_momentum", net=0.1):
    rid = state.store.start_backtest("2026-01-01", "2026-06-01", "5d", 0, digest, alpha=alpha)
    state.store.end_backtest(rid, "ok", {"net": net, "gross": net, "fees": 0.0,
                                         "slippage": 0.0, "turnover": 1.0, "periods": 10})
    return rid


def test_a_trial_is_a_distinct_question_not_a_run(client):
    """Re-running the same configuration is the same question asked twice: it
    gives the same answer, so it cannot raise the bar the winner has to clear.
    Changing anything the digest covers does."""
    c, state = client
    _ok_run(state, "d1")
    _ok_run(state, "d2")
    _ok_run(state, "d2")                     # same question again
    _ok_run(state, "d3", alpha="alpha_low_vol")

    assert c.get("/api/trials?alpha=alpha_momentum").json()["count"] == 2
    assert c.get("/api/trials").json()["count"] == 3


def test_the_ledger_keeps_what_was_refuted(client):
    """The losers are the count. A ledger holding only winners cannot be divided
    by anything."""
    c, state = client
    first = _ok_run(state, "d1")
    second = _ok_run(state, "d2", net=-0.04)
    r = c.post("/api/trials", json={
        "run_id": second, "hypothesis": "60d lookback instead of 20d",
        "parent_run_id": first, "disposition": "refuted",
        "proposed_by": "claude-opus-5",
    })
    assert r.status_code == 200 and r.json()["count"] == 2

    row = next(t for t in c.get("/api/trials").json()["trials"] if t["run_id"] == second)
    assert row["hypothesis"] == "60d lookback instead of 20d"
    assert row["parent_run_id"] == first
    assert row["disposition"] == "refuted"
    # and it still counts, which is the whole point
    assert c.get("/api/trials?alpha=alpha_momentum").json()["count"] == 2


def test_the_ledger_refuses_a_disposition_it_cannot_mean(client):
    c, state = client
    rid = _ok_run(state, "d1")
    assert c.post("/api/trials", json={"run_id": rid, "disposition": "great"}).status_code == 400
    assert c.post("/api/trials", json={"run_id": 999, "hypothesis": "x"}).status_code == 404


def test_a_report_carries_the_count_it_was_chosen_from(client):
    """A figure shown without it is not yet something anybody can judge."""
    c, state = client
    _ok_run(state, "d1")
    rid = _ok_run(state, "d2")
    body = c.get(f"/api/backtests/{rid}").json()
    assert body["trials"] == 2
    assert body["trials_all"] == 2


# ------------------------------------------------------------------- the bar
def test_the_bar_is_off_until_somebody_sets_it(client):
    c, _ = client
    body = c.get("/api/bar").json()
    assert body["bar"] is None or body["bar"]["rule"] == "none"


def test_setting_the_bar_writes_it_to_the_file(client):
    c, state = client
    r = c.put("/api/bar", json={"rule": "count", "alpha": 0.05, "gate": True})
    assert r.status_code == 200
    state.reload()
    bar = state.project.backtest.bar
    assert bar.rule == "count" and bar.gate is True
    assert c.get("/api/bar").json()["bar"]["rule"] == "count"


def test_loosening_the_bar_is_recorded_as_such(client):
    """Whoever proposes strategies should not be able to quietly lower the line
    that judges them. Not forbidden -- findable."""
    c, _ = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.01, "gate": True})
    r = c.put("/api/bar", json={"rule": "count", "alpha": 0.20, "gate": True})
    assert r.json()["loosened"] is True

    history = c.get("/api/bar").json()["last_changed"]
    assert history and history[0]["level"] == "warn"
    assert "→" in history[0]["message"]

    # and tightening it back is not a warning
    assert c.put("/api/bar", json={"rule": "count", "alpha": 0.01,
                                   "gate": True}).json()["loosened"] is False


def test_turning_the_gate_off_counts_as_loosening(client):
    c, _ = client
    c.put("/api/bar", json={"rule": "floor", "t_floor": 3.0, "gate": True})
    assert c.put("/api/bar", json={"rule": "floor", "t_floor": 3.0,
                                   "gate": False}).json()["loosened"] is True


def test_the_bar_refuses_a_significance_that_is_not_one(client):
    c, _ = client
    assert c.put("/api/bar", json={"rule": "count", "alpha": 1.5}).status_code >= 400
    assert c.put("/api/bar", json={"rule": "floor", "t_floor": -1}).status_code >= 400
    assert c.put("/api/bar", json={"rule": "nonsense"}).status_code >= 400


def test_a_report_says_what_the_bar_did_to_it(client):
    import json as _json

    c, state = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.05})
    #  A real replay stores its figures inside the report blob, which is what the
    #  endpoint reads -- so the fixture has to as well, or the verdict has no
    #  totals to judge and quietly reports nothing.
    totals = {"net": 0.1, "gross": 0.1, "fees": 0.0, "slippage": 0.0, "turnover": 1.0,
              "periods": 104, "sharpe": 2.23, "periods_per_year": 52.0}
    rid = state.store.start_backtest("2026-01-01", "2026-06-01", "5d", 0, "d1",
                                     alpha="alpha_momentum")
    state.store.end_backtest(rid, "ok", totals,
                             report=_json.dumps({"totals": totals, "run_id": rid}))
    verdict = c.get(f"/api/backtests/{rid}").json()["bar"]
    assert verdict["rule"] == "count"
    assert verdict["required"] is not None and verdict["t"] is not None


# ------------------------------------------------------------------- the loop
def test_a_pass_picks_the_least_tested_alpha_first(client):
    """An attack is worth most where nobody has attacked yet."""
    from qanat.research import target_alphas

    _, state = client
    for dig in ("a1", "a2", "a3"):
        _ok_run(state, dig, alpha="alpha_momentum")
    _ok_run(state, "b1", alpha="alpha_low_vol")

    assert target_alphas(state.store, state.project, 1) == ["alpha_low_vol"]
    assert target_alphas(state.store, state.project, 2) == ["alpha_low_vol", "alpha_momentum"]


def test_a_pass_refuses_to_invent_something_to_do(client):
    """A project with nothing priced has nothing to falsify, and a pass that woke
    to find that should say so rather than making work up."""
    c, _ = client
    r = c.post("/api/research/run", json={})
    assert r.status_code == 409
    assert "priced" in r.json()["detail"]


def test_the_research_goal_is_one_of_two_safe_ones(client):
    c, state = client
    _ok_run(state, "a1")
    assert c.post("/api/research/run", json={"goal": "search"}).status_code == 400
    assert c.post("/api/research/run", json={"goal": "invent"}).status_code == 400


def test_research_says_what_it_would_pick(client):
    """A schedule whose target nobody can predict is a schedule nobody trusts."""
    c, state = client
    _ok_run(state, "a1", alpha="alpha_low_vol")
    body = c.get("/api/research").json()
    assert body["would_target"] == ["alpha_low_vol"]
    assert body["running"] is None


def test_the_falsify_brief_forbids_improving_the_thing_it_measures(client):
    """A pass that improves what it was measuring has measured nothing."""
    from qanat.research import falsify_brief

    brief = falsify_brief("alpha_momentum", 0.123, 3)
    assert "+12.30%" in brief and "3 recorded" in brief
    assert "not to improve it" in brief
    assert "Do not change the strategy" in brief
    # and it must not be allowed to over-conclude from one bad window
    assert "One losing window is a result for that" in brief
    assert "/api/trials" in brief


def test_a_research_schedule_does_nothing_without_a_door(client):
    """The pass drives an agent over HTTP, so a Scheduler nobody handed an address
    to simply never researches -- rather than failing every minute."""
    from datetime import datetime, timezone

    from qanat.models import Research
    from qanat.scheduler import Scheduler

    _, state = client
    state.project.research = Research(enabled=True, schedule="* * * * *")
    sched = Scheduler(state.store, state.project, state.root)
    assert sched.research_due(datetime.now(timezone.utc)) is False

    sched.research_through(state, "http://127.0.0.1:8420")
    # first call only arms the clock; it does not fire retroactively
    assert sched.research_due(datetime.now(timezone.utc)) is False
    assert sched._research_at is not None


def _priced_run(state, digest, alpha="alpha_momentum", sharpe=0.6, periods=104):
    """A run whose figures live where the endpoint reads them: inside the report."""
    import json as _json

    totals = {"net": 0.1, "gross": 0.1, "fees": 0.0, "slippage": 0.0, "turnover": 1.0,
              "periods": periods, "sharpe": sharpe, "periods_per_year": 52.0}
    rid = state.store.start_backtest("2026-01-01", "2026-06-01", "5d", 0, digest, alpha=alpha)
    state.store.end_backtest(rid, "ok", totals,
                             report=_json.dumps({"totals": totals, "run_id": rid}))
    return rid


def test_the_gate_blocks_calling_a_weak_run_live(client):
    """The one thing worth refusing. A replay is a measurement and measuring is
    always allowed; writing `live` against it is a claim."""
    c, state = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.05, "gate": True})
    rid = _priced_run(state, "d1", sharpe=0.6)          # t ~ 0.85, nowhere near

    blocked = c.post("/api/trials", json={"run_id": rid, "disposition": "live"})
    assert blocked.status_code == 409
    assert "does not clear the bar" in blocked.json()["detail"]

    # everything else about the same run is still recordable
    assert c.post("/api/trials", json={
        "run_id": rid, "hypothesis": "baseline", "disposition": "candidate",
    }).status_code == 200


def test_the_gate_lets_a_strong_run_through(client):
    c, state = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.05, "gate": True})
    rid = _priced_run(state, "d1", sharpe=3.0, periods=520)   # t ~ 9.5
    assert c.post("/api/trials", json={"run_id": rid, "disposition": "live"}).status_code == 200


def test_reporting_only_refuses_nothing(client):
    """Off by default, and off means off: the verdict is shown beside the number
    for a person to weigh. A tool that refuses things on day one gets its bar set
    to `none` and left there."""
    c, state = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.05, "gate": False})
    rid = _priced_run(state, "d1", sharpe=0.6)
    assert c.post("/api/trials", json={"run_id": rid, "disposition": "live"}).status_code == 200


def test_an_unjudgeable_run_is_not_waved_through(client):
    """Too few periods to say anything is not the same as clearing the bar."""
    c, state = client
    c.put("/api/bar", json={"rule": "count", "alpha": 0.05, "gate": True})
    rid = _priced_run(state, "d1", sharpe=None, periods=1)
    r = c.post("/api/trials", json={"run_id": rid, "disposition": "live"})
    assert r.status_code == 409 and "cannot be judged" in r.json()["detail"]


def test_a_cli_we_cannot_drive_is_not_on_the_shelf(client):
    """`cursor-agent` was listed and did not work: it was given only `--print`,
    whose default is plain text, while the stream parser reads JSON lines -- so
    every answer was dropped. It also refuses to start until Workspace Trust is
    granted interactively, which a headless pass cannot do.

    Adding a CLI needs an argv, an event mapping and a tool fence. Until all three
    exist, it does not belong on the list."""
    from qanat.headless import CLIS

    assert {c["bin"] for c in CLIS} == {"claude"}
    c, _ = client
    assert c.put("/api/agent", json={"cli": "cursor-agent"}).status_code >= 400


def test_a_pass_owns_the_replays_it_runs(client):
    """Without this the pass's own trials were filed against the console's session
    or against nothing, so a pass could run seven and its row would say none."""
    from qanat.api import _owning_session
    from qanat.research import Pass

    class Req:
        def __init__(self, ui):
            self.headers = {"x-qanat-ui": "1"} if ui else {}

    _, state = client
    state._session = "human-session"
    assert _owning_session(state, Req(True)) == "human-session"
    assert _owning_session(state, Req(False)) == "human-session"

    state._research = Pass("falsify", ["alpha_momentum"])
    #  A stamped request is a person clicking, even mid-pass.
    assert _owning_session(state, Req(True)) == "human-session"
    #  An unstamped one, while a pass runs, is the pass.
    assert _owning_session(state, Req(False)) == state._research.session_id

    state._research.done = True
    assert _owning_session(state, Req(False)) == "human-session"


def test_a_report_says_which_alpha_it_is(client):
    """The report blob does not carry it, so without this the page had the figures
    and no idea whose they were -- and the ledger beside them nothing to key on."""
    c, state = client
    rid = _priced_run(state, "d1", alpha="alpha_low_vol")
    assert c.get(f"/api/backtests/{rid}").json()["alpha"] == "alpha_low_vol"


# The console is gone and its session browser with it. What the store does for a
# session is still reached, by the unattended pass in `research.py`, so the part
# that survived keeps its own tests rather than losing coverage with the routes.
def test_a_session_round_trips_without_any_console(client):
    _, state = client
    state.store.open_session("s-9", cli="claude", title="falsify pass")
    state.store.note_ask("s-9", cost_usd=0.12, model="opus", title="falsify · momentum")
    state.store.save_ask("s-9", {"question": "break it", "answer": "could not"})
    state.store.end_session("s-9", "nothing survived")

    row = next(r for r in state.store.sessions() if r["session_id"] == "s-9")
    assert row["title"] == "falsify pass"
    assert row["summary"] == "nothing survived"
    assert float(row["cost_usd"]) == pytest.approx(0.12)


def test_the_installed_clis_are_discoverable_without_the_api():
    """`/api/ask` used to report this. The pass still needs to find a CLI."""
    from qanat.headless import CLIS, list_clis

    found = list_clis()
    assert {c["bin"] for c in found} == {c["bin"] for c in CLIS}
    for entry in found:
        assert "found" in entry and "support" in entry
