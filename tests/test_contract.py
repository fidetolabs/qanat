"""The stage contract is the product. These tests are what it means."""

from pathlib import Path

import pytest

from qanat.models import Project
from qanat.project import validate
from qanat.scaffold import write_project

BASE = {
    "project": "t",
    "stages": [
        {"id": "raw", "kind": "raw"},
        {"id": "features", "kind": "features"},
        {"id": "weights", "kind": "weights"},
    ],
    "sources": [{"id": "s", "to": ["raw.bars"], "connector": "synthetic"}],
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "x.py").write_text("def run(ctx):\n    return {}\n")
    return tmp_path


def check(spec: dict, root: Path):
    return validate(Project.model_validate(spec), root)


def test_starter_project_holds(tmp_path: Path):
    write_project(tmp_path, "demo")
    from qanat.project import load

    project, r = load(tmp_path)
    assert validate(project, r).ok


def test_raw_is_never_edited(root: Path):
    spec = {**BASE, "steps": [
        {"id": "bad", "from": ["raw.bars"], "to": ["raw.bars2"], "script": "steps/x.py"}]}
    rep = check(spec, root)
    assert any("never edited" in e for e in rep.errors)


def test_data_only_moves_forward(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["features.f"], "script": "steps/x.py"},
        {"id": "b", "from": ["weights.target"], "to": ["features.g"], "script": "steps/x.py"},
        {"id": "c", "from": ["features.f"], "to": ["weights.target"], "script": "steps/x.py"},
    ]}
    rep = check(spec, root)
    assert any("only moves forward" in e for e in rep.errors)


def test_the_weights_stage_holds_one_table_per_alpha(root: Path):
    """Two alphas are two portfolios, and the plan's layer contract says so: the
    weights stage holds one table per alpha, not one table in total."""
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["weights.one"], "script": "steps/x.py"},
        {"id": "b", "from": ["raw.bars"], "to": ["weights.two"], "script": "steps/x.py"},
    ]}
    assert check(spec, root).ok


def test_one_step_writes_one_portfolio(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["weights.one", "weights.two"],
         "script": "steps/x.py"},
    ]}
    assert any("One alpha is one portfolio" in e for e in check(spec, root).errors)


def test_an_alpha_cannot_read_another_alpha(root: Path):
    """Alphas never stack — an edge counted through a tower of them is counted twice."""
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["weights.one"], "script": "steps/x.py"},
        {"id": "b", "from": ["weights.one"], "to": ["weights.two"], "script": "steps/x.py"},
    ]}
    assert any("never stack" in e for e in check(spec, root).errors)


def test_weights_stage_must_be_last(root: Path):
    spec = {**BASE, "stages": [
        {"id": "raw", "kind": "raw"},
        {"id": "weights", "kind": "weights"},
        {"id": "features", "kind": "features"},
    ]}
    assert any("must be last" in e for e in check(spec, root).errors)


def test_every_read_has_a_producer(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.nope"], "to": ["weights.target"], "script": "steps/x.py"}]}
    assert any("which nothing produces" in e for e in check(spec, root).errors)


def test_feature_stage_may_chain(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["features.f1"], "script": "steps/x.py"},
        {"id": "b", "from": ["features.f1"], "to": ["features.f2"], "script": "steps/x.py"},
        {"id": "c", "from": ["features.f2"], "to": ["weights.target"], "script": "steps/x.py"},
    ]}
    assert check(spec, root).ok


def test_sql_step_writes_one_table(root: Path):
    (root / "steps" / "y.sql").write_text("SELECT 1")
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["features.f", "features.g"], "script": "steps/y.sql"},
        {"id": "b", "from": ["features.f"], "to": ["weights.target"], "script": "steps/x.py"},
    ]}
    assert any("writes exactly one table" in e for e in check(spec, root).errors)
