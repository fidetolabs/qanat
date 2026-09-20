"""Retention policies and project I/O."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qanat.project import load, validate
from qanat.project_io import dump_project, save_project
from qanat.retention import apply_retention, parse_duration, run_retention
from qanat.scaffold import write_project
from qanat.store import Store


def test_parse_duration():
    assert parse_duration("7d") == timedelta(days=7)
    assert parse_duration("24h") == timedelta(hours=24)
    with pytest.raises(ValueError):
        parse_duration("nope")


def test_retention_drops_old_rows(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    store.write("raw.daily_prices", __import__("pandas").DataFrame({
        "ts": [
            datetime.now(timezone.utc) - timedelta(days=10),
            datetime.now(timezone.utc) - timedelta(days=1),
        ],
        "symbol": ["AAPL", "MSFT"],
        "close": [100.0, 200.0],
    }))
    project.retention = {"raw.daily_prices": "7d"}
    removed = apply_retention(store, "raw.daily_prices", "7d")
    assert removed == 1
    assert store.table_info("raw.daily_prices").rows == 1
    store.close()


def test_project_save_roundtrip(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    project.retention = {"normalized.prices": "30d"}
    save_project(project, root)
    again, _ = load(tmp_path)
    assert again.retention == {"normalized.prices": "30d"}
    assert validate(again, root).ok


def test_dump_project_uses_yaml_names(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, _root = load(tmp_path)
    data = dump_project(project)
    assert "project" in data
    assert data["sources"][0]["to"]
    assert data["steps"][0]["from"]


def test_run_retention_from_project(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    import pandas as pd

    store.write("raw.news", pd.DataFrame({
        "ts": [datetime.now(timezone.utc) - timedelta(days=30)],
        "symbol": ["AAPL"],
        "sentiment": [0.5],
    }))
    project.retention = {"raw.news": "7d"}
    out = run_retention(store, project)
    assert out["raw.news"] == 1
    store.close()


def test_saving_a_step_keeps_the_rest_of_the_file(tmp_path: Path):
    """Everything the model holds must survive a round trip, or editing one step
    silently deletes the block next to it."""
    from qanat.editor import save_step
    from qanat.project import load
    from qanat.scaffold import write_project

    write_project(tmp_path, "demo")
    (tmp_path / "qanat.yaml").write_text(
        (tmp_path / "qanat.yaml").read_text() + "\ntime_columns:\n  raw.daily_prices: ts\n"
    )
    before, root = load(tmp_path)
    assert before.backtest is not None and before.time_columns

    save_step(before, root, {"id": "momentum", "from": ["normalized.prices"],
                             "to": ["features.momentum"], "script": "steps/momentum.py",
                             "options": {"lookback": 30}}, create_script=False)

    after, _ = load(tmp_path)
    assert after.backtest == before.backtest
    assert after.time_columns == before.time_columns
    assert after.job("momentum").options["lookback"] == 30


def test_saving_keeps_every_field_a_job_declares(tmp_path: Path):
    """A console save rewrites the whole file. Anything the dumper forgets is
    deleted from the project without anybody asking for it."""
    from qanat.models import Project
    from qanat.project_io import dump_project, project_from_dict

    spec = {
        "project": "p", "store": "./d.duckdb",
        "stages": [{"id": "raw", "kind": "raw"}, {"id": "f", "kind": "features"},
                   {"id": "w", "kind": "weights"}],
        "sources": [{"id": "s", "to": ["raw.t"], "connector": "synthetic",
                     "key": ["symbol", "ts"], "schedule": "*/5 * * * *"}],
        "steps": [{"id": "a", "from": ["raw.t"], "to": ["w.a"], "script": "a.py",
                   "when": ["raw.t"], "rebalance": "20d", "decay": 4}],
        "backtest": {"prices": "raw.t", "live": True, "live_from": "2026-06-01"},
    }
    before = Project.model_validate(spec)
    after = project_from_dict(dump_project(before))

    assert after.job("s").key == ["symbol", "ts"], "a source lost its dedupe key"
    assert after.job("a").when == ["raw.t"], "a step lost what it waits on"
    assert after.backtest.live is True
    assert after.backtest.live_from == "2026-06-01", "the frontier was not written back"


COMMENTED = """\
# What this project is, and why anybody should trust it.
#
#   qanat run
project: commented
store: ./data/qanat.duckdb
stages:
- id: raw
  kind: raw
- id: normalized
  kind: features
- id: weights
  kind: weights
sources:
- id: bars                       # the only feed
  to:
  - raw.bars
  connector: synthetic
  # landed exactly as it arrived, never edited
  options:
    series: bars
steps:
- id: normalize
  from:
  - raw.bars
  to:
  - normalized.prices
  script: steps/normalize.sql
"""


def _comments(text):
    return [l.strip() for l in text.splitlines() if l.strip().startswith("#")]


def test_saving_keeps_the_comments_somebody_wrote(tmp_path):
    """An edit must not delete the notes in qanat.yaml.

    `yaml.safe_dump` cannot round-trip a comment, so the first edit an agent made
    stripped every one -- including the header on `examples/fx-bundled` saying
    what the dataset is. A tool that deletes your notes on its way past is one you
    cannot leave alone with the file.
    """
    (tmp_path / "qanat.yaml").write_text(COMMENTED)
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "normalize.sql").write_text("SELECT 1")
    before = (tmp_path / "qanat.yaml").read_text()

    project, root = load(tmp_path)
    project.steps[0].options = {"lookback": 20}      # the smallest real edit
    save_project(project, root)

    after = (tmp_path / "qanat.yaml").read_text()
    assert _comments(after) == _comments(before)
    assert after.startswith("# What this project is")
    assert "# the only feed" in after
    assert "lookback: 20" in after


def test_a_save_still_says_exactly_what_the_project_holds(tmp_path):
    """Comments are kept, but never at the cost of the data being right."""
    (tmp_path / "qanat.yaml").write_text(COMMENTED)
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "normalize.sql").write_text("SELECT 1")

    project, root = load(tmp_path)
    project.steps = []                                # removals have to land too
    save_project(project, root)

    reloaded, _ = load(root)
    assert reloaded.steps == []
    assert dump_project(reloaded) == dump_project(project)


def test_repeated_saves_do_not_drift(tmp_path):
    """An editor writes on every change, so save-load-save has to be a fixed point."""
    (tmp_path / "qanat.yaml").write_text(COMMENTED)
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "normalize.sql").write_text("SELECT 1")

    project, root = load(tmp_path)
    save_project(project, root)
    once = (tmp_path / "qanat.yaml").read_text()
    project, root = load(root)
    save_project(project, root)
    assert (tmp_path / "qanat.yaml").read_text() == once


def test_it_still_saves_without_ruamel(tmp_path, monkeypatch):
    """The dependency is optional: an older install keeps working, minus comments."""
    import qanat.project_io as pio

    (tmp_path / "qanat.yaml").write_text(COMMENTED)
    (tmp_path / "steps").mkdir()
    (tmp_path / "steps" / "normalize.sql").write_text("SELECT 1")
    monkeypatch.setattr(pio, "_round_tripper", lambda: None)

    project, root = load(tmp_path)
    save_project(project, root)
    reloaded, _ = load(root)
    assert reloaded.name == "commented"
