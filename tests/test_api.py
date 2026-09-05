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
    return TestClient(create_app(state)), state


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
