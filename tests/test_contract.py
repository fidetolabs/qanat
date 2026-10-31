"""The stage contract is the product. These tests are what it means."""

from pathlib import Path

import pytest
from conftest import write_demo_project as write_project

from qanat.models import Project
from qanat.project import validate

#: Three stages whose names are this project's own. Only `raw` means anything
#: to the platform; `features` and `picks` are words a user chose, and the
#: contract enforces properties rather than names. It used to enforce an
#: ordering on two more -- `weights` and `pnl` -- and those two stage kinds left
#: the engine in 0.5.0 while their rules stayed behind.
BASE = {
    "project": "t",
    "stages": [
        {"id": "raw", "kind": "raw"},
        {"id": "features", "kind": "features"},
        {"id": "picks", "kind": "whatever-this-project-calls-it"},
    ],
    "sources": [{"id": "s", "to": ["raw.bars"], "connector": "csv", "options": {"path": "./seed/x.csv"}}],
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
        {"id": "b", "from": ["picks.target"], "to": ["features.g"], "script": "steps/x.py"},
        {"id": "c", "from": ["features.f"], "to": ["picks.target"], "script": "steps/x.py"},
    ]}
    rep = check(spec, root)
    assert any("only moves forward" in e for e in rep.errors)


def test_every_read_has_a_producer(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.nope"], "to": ["picks.target"], "script": "steps/x.py"}]}
    assert any("which nothing produces" in e for e in check(spec, root).errors)


def test_feature_stage_may_chain(root: Path):
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["features.f1"], "script": "steps/x.py"},
        {"id": "b", "from": ["features.f1"], "to": ["features.f2"], "script": "steps/x.py"},
        {"id": "c", "from": ["features.f2"], "to": ["picks.target"], "script": "steps/x.py"},
    ]}
    assert check(spec, root).ok


def test_sql_step_writes_one_table(root: Path):
    (root / "steps" / "y.sql").write_text("SELECT 1")
    spec = {**BASE, "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["features.f", "features.g"], "script": "steps/y.sql"},
        {"id": "b", "from": ["features.f"], "to": ["picks.target"], "script": "steps/x.py"},
    ]}
    assert any("writes exactly one table" in e for e in check(spec, root).errors)


def test_no_stage_kind_carries_an_ordering_rule_any_more(root: Path):
    """`weights` and `pnl` left the engine in 0.5.0 and their rules did not.

    A kind is a name the project chose, so a rule keyed on one of five fixed
    words could only ever be enforced against a project that happened to use
    them -- and two of those words name nothing the platform does.
    """
    spec = {**BASE, "stages": [
        {"id": "raw", "kind": "raw"},
        {"id": "pnl", "kind": "pnl"},
        {"id": "weights", "kind": "weights"},
    ], "steps": [
        {"id": "a", "from": ["raw.bars"], "to": ["pnl.anything"], "script": "steps/x.py"},
        {"id": "b", "from": ["pnl.anything"], "to": ["weights.two"], "script": "steps/x.py"},
    ]}
    rep = check(spec, root)
    assert rep.ok, rep.errors

    source = (Path(__file__).resolve().parents[1] / "src" / "qanat" / "project.py").read_text()
    enforcing = [
        line.strip() for line in source.splitlines()
        if ("weights" in line or "pnl" in line) and "r.errors.append" in line
    ]
    assert enforcing == [], f"still enforced: {enforcing}"
