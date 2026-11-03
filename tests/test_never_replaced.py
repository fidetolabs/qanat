"""No job replaces provided rows.

A row that has been provided stays. A later pass that disagrees with it writes a
new row and does not overwrite the old one, because **an answer was given to
somebody and a store that cannot say what it told them cannot stand behind it.**
Deleting is allowed and is always something a person asks for.

The cost the decision names is exactly the thing these tests hold: *every reader
now has to pick*, and the risk is not size but a reader that forgets to and
silently returns two answers for one day.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pandas as pd
import pytest
from conftest import write_project_text

from qanat.project import ProjectError, load, validate
from qanat.store import Store

YAML = """\
project: k
store: {store}
stages:
  - {{id: raw, kind: raw}}
time_columns:
  raw.prices: as_of
ingestion_job_definitions:
  - id: land
    to: [raw.prices]
    connector: seed
    key: [symbol, as_of]
    options: {{path: ./seed/prices.csv}}
"""


def _project(tmp_path: Path, rows: str):
    from conftest import STORE_KIND, write_project_text

    store_line = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed" / "prices.csv").write_text(rows)
    write_project_text(tmp_path, YAML.format(store=store_line))
    return load(tmp_path)


# ---- the refusal -------------------------------------------------------------
def test_mode_replace_is_refused_with_the_fix_in_the_message(tmp_path: Path):
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "prices.csv").write_text("symbol,close,as_of\nA,1,2024-01-01\n")
    write_project_text(tmp_path, textwrap.dedent("""\
        project: k
        store: ./data
        stages:
          - {id: raw, kind: raw}
        ingestion_job_definitions:
          - {id: land, to: [raw.prices], connector: seed, mode: replace,
             options: {path: ./seed/prices.csv}}
        """))
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    said = str(exc.value)
    assert "mode: replace is not allowed" in said
    assert "mode: append" in said and "key:" in said


def test_an_append_with_no_key_is_warned_about(tmp_path: Path):
    """Not refused -- a feed of distinct events is a real thing -- but said out
    loud, because without a key every poll keeps a full copy."""
    (tmp_path / "seed").mkdir()
    (tmp_path / "seed" / "prices.csv").write_text("symbol,close,as_of\nA,1,2024-01-01\n")
    write_project_text(tmp_path, textwrap.dedent("""\
        project: k
        store: ./data
        stages:
          - {id: raw, kind: raw}
        ingestion_job_definitions:
          - {id: land, to: [raw.prices], connector: seed,
             options: {path: ./seed/prices.csv}}
        """))
    project, root = load(tmp_path)
    rep = validate(project, root)
    assert any("no `key:`" in w for w in rep.warnings), rep.warnings


# ---- a correction is a new row -----------------------------------------------
def test_a_re_fetch_with_a_changed_value_leaves_both_rows_readable(tmp_path: Path):
    """[prices-are-rewritten] -- a vendor serves today's adjusted history rather
    than the close as it printed, so a re-fetch finding a different number for a
    day already stored is the ordinary case, not an error."""
    project, root = _project(tmp_path, "symbol,close,as_of\nA,100,2024-01-01\n")
    from qanat.runner import run_ingestion

    store = Store(project.store_url(root))
    try:
        definition = project.ingestion_job_definitions[0]
        assert run_ingestion(store, project, root, definition).ok

        (root / "seed" / "prices.csv").write_text(
            "symbol,close,as_of\nA,101.5,2024-01-01\n")
        second = run_ingestion(store, project, root, definition)
        assert second.ok and second.rows == 1, "the correction landed as a row"

        plain = store.read("raw.prices")
        assert len(plain) == 1, "a plain read returns one row for one day"
        assert float(plain["close"].iloc[0]) == 101.5, "and it is the newer one"

        held = store.con.execute(
            f'SELECT close FROM "{store.home}"."raw__prices" ORDER BY close').df()
        assert [float(x) for x in held["close"]] == [100.0, 101.5], \
            "the number an answer already rested on is still there"

        # And the disagreement is attributable: two executions, one each.
        out = store.provenance("raw.prices")
        assert sum(e["rows"] for e in out["executions"]) == 2
        assert len({e["execution"] for e in out["executions"]}) == 2
    finally:
        store.close()


def test_the_same_values_arriving_again_are_not_stored_twice(tmp_path: Path):
    """Idempotency is not replacement. Re-ingesting the same row by key lands the
    same values, and that is not a second row."""
    project, root = _project(tmp_path, "symbol,close,as_of\nA,100,2024-01-01\n")
    from qanat.runner import run_ingestion

    store = Store(project.store_url(root))
    try:
        definition = project.ingestion_job_definitions[0]
        assert run_ingestion(store, project, root, definition).rows == 1
        again = run_ingestion(store, project, root, definition)
        assert again.ok and again.rows == 0, "nothing new arrived"
        held = store.con.execute(
            f'SELECT count(*) FROM "{store.home}"."raw__prices"').fetchone()[0]
        assert held == 1
    finally:
        store.close()


def test_a_read_at_a_past_date_shows_what_was_knowable_then(tmp_path: Path):
    """The clock first, then the pick. Built the other way round, a screen would
    ask "which row is current today" and filter *that* by date -- so a value
    corrected last week would vanish at a date before the correction."""
    project, root = _project(tmp_path, "symbol,close,as_of\nA,100,2024-01-01\n")
    from qanat.runner import run_ingestion

    store = Store(project.store_url(root))
    try:
        definition = project.ingestion_job_definitions[0]
        run_ingestion(store, project, root, definition)
        (root / "seed" / "prices.csv").write_text(
            "symbol,close,as_of\nA,100,2024-01-01\nA,200,2024-02-01\n")
        run_ingestion(store, project, root, definition)

        store.open_pit("2024-01-15", project.time_columns)
        try:
            early = store.read("raw.prices")
            assert len(early) == 1
            assert float(early["close"].iloc[0]) == 100.0
        finally:
            store.close_pit()

        assert len(store.read("raw.prices")) == 2
    finally:
        store.close()


# ---- `run` is safe to call ----------------------------------------------------
def test_run_loses_no_row_landed_outside_the_selection(tmp_path: Path):
    """**The defect this task exists for.** On a scaffolded project `run` with no
    argument rewrote the documents table to only what the selection returned, so
    every filing ingested ad hoc outside that list was deleted as the normal
    outcome of a job that reported ok -- and the honest advice became to avoid
    the platform's main verb."""
    project, root = _project(tmp_path, "symbol,close,as_of\nA,100,2024-01-01\n")
    from qanat.runner import run_all

    store = Store(project.store_url(root))
    try:
        assert all(r.ok for r in run_all(store, project, root))

        # A row nobody's selection asks for, landed by hand.
        store.write("raw.prices", pd.DataFrame({
            "symbol": ["ZZZZ"], "close": [7.0],
            "as_of": pd.to_datetime(["2023-06-01"])}), mode="append",
            key=["symbol", "as_of"])
        assert len(store.read("raw.prices")) == 2

        assert all(r.ok for r in run_all(store, project, root))
        kept = set(store.read("raw.prices")["symbol"])
        assert kept == {"A", "ZZZZ"}, "the hand-landed row survived the run"
    finally:
        store.close()


# ---- removal is a verb a person runs ------------------------------------------
def test_the_clock_applies_no_retention():
    """Retention used to be applied by the scheduler loop once a minute, which
    made removal a side effect of the clock running."""
    import inspect

    from qanat.scheduler import Scheduler

    # `parse_duration` lives in that module and is what reads a timeout, so the
    # name appears; what must not appear is anything applying a policy.
    src = inspect.getsource(Scheduler)
    assert "run_retention" not in src
    assert "apply_retention" not in src
    assert "prune" not in src


def test_retention_says_what_it_would_remove_without_removing_it(tmp_path: Path):
    from qanat.retention import run_retention, would_remove

    project, root = _project(tmp_path, "symbol,close,as_of\nA,100,2020-01-01\n")
    project.retention = {"raw.prices": "1h"}
    from qanat.runner import run_ingestion

    store = Store(project.store_url(root))
    try:
        run_ingestion(store, project, root, project.ingestion_job_definitions[0])
        said = would_remove(store, project)
        assert said[0]["table"] == "raw.prices"
        assert said[0]["rows"] == 1
        assert len(store.read("raw.prices")) == 1, "saying it removed nothing"

        assert run_retention(store, project) == {"raw.prices": 1}
        assert len(store.read("raw.prices")) == 0
    finally:
        store.close()
