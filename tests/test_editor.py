"""Retention policies and project I/O."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import write_demo_project as write_project
from conftest import write_project_text

from qanat.project import load, validate
from qanat.project_io import dump_project, save_project
from qanat.retention import apply_retention, parse_duration, run_retention
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
    """A save writes the part files and **nothing else**.

    `retention:` is `qanat.yaml`'s, so changing it in memory and saving does not
    write it -- which is the point rather than a gap: the architecture file is
    not something a tool edits, and a verb that silently rewrote it is the
    defect this split exists to close.
    """
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    before = (root / "qanat.yaml").read_text()

    project.transform_job_definitions[0].options = {"lookback": 99}
    project.retention = {"normalized.prices": "30d"}
    written = save_project(project, root)

    assert (root / "qanat.yaml") not in written
    assert (root / "qanat.yaml").read_text() == before, "the architecture file is untouched"

    again, _ = load(tmp_path)
    edited = project.transform_job_definitions[0].id
    assert again.definition(edited).options == {"lookback": 99}, "the part file saved"
    assert again.retention == {}, "and the architecture key was never a tool's to write"
    assert validate(again, root).ok


def test_dump_project_uses_yaml_names(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, _root = load(tmp_path)
    data = dump_project(project)
    assert "project" in data
    assert data["ingestion_job_definitions"][0]["to"]
    assert data["transform_job_definitions"][0]["from"]


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
    from conftest import write_demo_project as write_project

    from qanat.editor import save_transform
    from qanat.project import load

    write_project(tmp_path, "demo")
    write_project_text(tmp_path, 
        (tmp_path / "qanat.yaml").read_text() + "\ntime_columns:\n  raw.daily_prices: ts\n"
    )
    before, root = load(tmp_path)
    assert before.time_columns

    save_transform(before, root, {"id": "momentum", "from": ["normalized.prices"],
                             "to": ["features.momentum"], "script": "transforms/momentum.py",
                             "options": {"lookback": 30}}, create_script=False)

    after, _ = load(tmp_path)
    assert after.time_columns == before.time_columns
    assert after.definition("momentum").options["lookback"] == 30


def test_saving_keeps_every_field_a_job_declares(tmp_path: Path):
    """A save rewrites the whole file. Anything the dumper forgets is deleted
    from the project without anybody asking for it."""
    from qanat.models import Project
    from qanat.project_io import dump_project, project_from_dict

    spec = {
        "project": "p", "store": "./d.duckdb",
        "stages": [{"id": "raw", "kind": "raw"}, {"id": "f", "kind": "features"},
                   {"id": "w", "kind": "weights"}],
        "ingestion_job_definitions": [
            {"id": "s", "to": ["raw.t"], "connector": "seed",
             "options": {"path": "./seed/x.csv"}, "key": ["symbol", "ts"]}],
        "transform_job_definitions": [
            {"id": "a", "from": ["raw.t"], "to": ["w.a"], "script": "a.py",
             "when": ["raw.t"]}],
        # The cadence is its own object now, so it is its own round trip.
        "job_schedules": [{"id": "s_5m", "definition": "s", "cron": "*/5 * * * *"}],
    }
    before = Project.model_validate(spec)
    after = project_from_dict(dump_project(before))

    assert after.definition("s").key == ["symbol", "ts"], \
        "an ingestion job lost its dedupe key"
    assert after.definition("a").when == ["raw.t"], "a transform lost what it waits on"
    assert [(x.id, x.definition, x.cron) for x in after.job_schedules] == \
        [("s_5m", "s", "*/5 * * * *")], "the cadence did not survive the round trip"


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
ingestion_job_definitions:
- id: bars                       # the only feed
  to:
  - raw.bars
  connector: seed
  # landed exactly as it arrived, never edited
  options:
    series: bars
transform_job_definitions:
- id: normalize
  from:
  - raw.bars
  to:
  - normalized.prices
  script: transforms/normalize.sql
"""


def _comments(text):
    return [l.strip() for l in text.splitlines() if l.strip().startswith("#")]


def test_a_save_never_touches_the_architecture_file(tmp_path):
    """The notes in `qanat.yaml` survive because nothing writes it.

    `yaml.safe_dump` cannot round-trip a comment, so the first edit an agent made
    stripped every one -- including the header on a bundled example saying what
    the dataset is. The round-tripper came first and kept most of them; the file
    split is the actual answer, because a key a tool *removed* has nowhere left
    to keep the note that sat above it. A tool that deletes your notes on its way
    past is one you cannot leave alone with the file, so it is not left alone
    with it.
    """
    write_project_text(tmp_path, COMMENTED)
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "normalize.sql").write_text("SELECT 1")
    architecture = (tmp_path / "qanat.yaml").read_text()

    project, root = load(tmp_path)
    project.transform_job_definitions[0].options = {"lookback": 20}      # the smallest real edit
    save_project(project, root)

    assert (tmp_path / "qanat.yaml").read_text() == architecture, "byte-identical"
    assert "# What this project is" in architecture
    assert "lookback: 20" in (tmp_path / "jobs.yaml").read_text()


def test_a_part_file_keeps_its_comments_where_it_can(tmp_path):
    """**Attempted, not promised**, and the difference is the point of the split.

    A tool writes these files, so a note above a key the tool removed has nowhere
    to land. One above a key that stays survives, because the round-tripper lays
    the new values onto the tree it parsed. That is the best a tool-written file
    can do -- which is why the file holding somebody's actual notes is not one.
    """
    write_project_text(tmp_path, COMMENTED)
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "normalize.sql").write_text("SELECT 1")
    jobs = tmp_path / "jobs.yaml"
    jobs.write_text("# polls once a day, which is all EDGAR publishes\n" + jobs.read_text())
    before = jobs.read_text()

    project, root = load(tmp_path)
    project.transform_job_definitions[0].options = {"lookback": 20}
    save_project(project, root)

    after = jobs.read_text()
    assert _comments(after) == _comments(before)
    assert "lookback: 20" in after


def test_a_save_still_says_exactly_what_the_project_holds(tmp_path):
    """Comments are kept, but never at the cost of the data being right."""
    write_project_text(tmp_path, COMMENTED)
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "normalize.sql").write_text("SELECT 1")

    project, root = load(tmp_path)
    project.transform_job_definitions = []                                # removals have to land too
    save_project(project, root)

    reloaded, _ = load(root)
    assert reloaded.transform_job_definitions == []
    assert dump_project(reloaded) == dump_project(project)


def test_repeated_saves_do_not_drift(tmp_path):
    """An editor writes on every change, so save-load-save has to be a fixed point."""
    write_project_text(tmp_path, COMMENTED)
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "normalize.sql").write_text("SELECT 1")

    project, root = load(tmp_path)
    save_project(project, root)
    once = (tmp_path / "qanat.yaml").read_text()
    project, root = load(root)
    save_project(project, root)
    assert (tmp_path / "qanat.yaml").read_text() == once


def test_it_still_saves_without_ruamel(tmp_path, monkeypatch):
    """The dependency is optional: an older install keeps working, minus comments."""
    import qanat.project_io as pio

    write_project_text(tmp_path, COMMENTED)
    (tmp_path / "transforms").mkdir()
    (tmp_path / "transforms" / "normalize.sql").write_text("SELECT 1")
    monkeypatch.setattr(pio, "_round_tripper", lambda: None)

    project, root = load(tmp_path)
    save_project(project, root)
    reloaded, _ = load(root)
    assert reloaded.name == "commented"
