"""`qanat.yaml` is what a person writes, and nothing here rewrites it.

**Measured, on the `check` project.** `define_metric` loaded `qanat.yaml`,
mutated the model and re-dumped the whole file at 17:59 on 2026-10-31. What the
re-dump destroyed: the note explaining why a row needs a publication date, the
note naming which tables the reading tools open, the section spacing, and the
hand-wrapped metric SQL. The project's own header -- *"A project for checking
the-agent-surface acceptance criteria"* -- was in `qanat.yaml.bak` and not in
the file.

Every authoring verb had that shape: `save_source`, `save_step`, `save_universe`,
`define_metric`, `remove_*`. They all loaded, mutated, re-dumped. The boundary is
not tidiness -- it is **human-written against tool-written**, and the two were
sharing a file.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

import pytest
from conftest import write_demo_project as write_project

from qanat import editor, filings, metrics
from qanat.project import ProjectError, load
from qanat.project_io import ARCHITECTURE, PARTS, part_for, save_project


def test_the_two_halves_of_a_project_do_not_overlap():
    owned = [k for keys in PARTS.values() for k in keys]
    assert len(owned) == len(set(owned)), "two files claiming one key"
    assert not set(owned) & set(ARCHITECTURE), "a key cannot be in both halves"


def test_what_stays_is_what_a_person_writes():
    """The project name, where the data lives, the stages, the clock, the corpus,
    what a screen prices against, and what gets dropped. Nothing a tool authors."""
    assert set(ARCHITECTURE) == {
        "project", "store", "stages", "time_columns", "corpus", "returns",
        "retention", "job_timeout",
    }


def test_definitions_schedules_metrics_and_universes_are_all_tool_written():
    for key in ("ingestion_job_definitions", "transform_job_definitions",
                "job_schedules", "metrics", "universes"):
        assert part_for(key), key


# ---- the rule, held by a test rather than by review --------------------------
def test_no_authoring_verb_has_a_write_path_to_the_architecture_file():
    """Checked by reading the code, because the failure mode is a verb that
    writes the file *correctly* on the path everybody tests and ruinously on one
    nobody does."""
    verbs = [v for name, v in vars(editor).items()
             if callable(v) and name.startswith(("save_", "delete_", "remove_"))
             and getattr(v, "__module__", "") == "qanat.editor"]
    verbs += [metrics.define, filings.subscribe, filings.unsubscribe]
    assert verbs, "nothing was found to check, which is the test failing silently"
    for verb in verbs:
        # Read as code, not as text. A docstring may name the file -- that is how
        # the rule gets explained -- and a string literal holding it is the thing
        # being refused.
        tree = ast.parse(textwrap.dedent(inspect.getsource(verb)))
        said = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and "qanat.yaml" in node.value
            and node is not getattr(tree.body[0], "_docstring", None)
        ]
        said = [s for s in said if s is not ast.get_docstring(tree.body[0])]
        assert not said, f"{verb.__name__} names the architecture file in code: {said}"


def test_save_project_writes_only_the_part_files(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    written = save_project(project, root)
    assert {p.name for p in written} <= set(PARTS)
    assert "qanat.yaml" not in {p.name for p in written}


def test_defining_a_metric_leaves_the_architecture_file_byte_identical(tmp_path: Path):
    """**The test case the task named.** `define_metric` is the verb that did the
    damage, so it is the one the rule is measured on."""
    from qanat.store import Store

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    before = (root / "qanat.yaml").read_text()
    assert before.lstrip().startswith("#"), "the fixture has a comment to lose"

    store = Store(project.store_url(root))
    try:
        from qanat.runner import run_all

        run_all(store, project, root)
        metrics.define(store, project, root, mid="spread",
                       sql="SELECT symbol AS subject, close AS value FROM normalized__prices")
    finally:
        store.close()

    assert (root / "qanat.yaml").read_text() == before
    assert "spread" in (root / "metrics.yaml").read_text()


def test_subscribing_leaves_the_architecture_file_byte_identical(tmp_path: Path):
    from qanat.scaffold import write_project as scaffold

    scaffold(tmp_path, "demo")
    project, root = load(tmp_path)
    before = (root / "qanat.yaml").read_text()

    out = filings.subscribe(project, root, ["AAPL"], forms=["10-K"], hour=6)

    assert (root / "qanat.yaml").read_text() == before
    assert out["subscribed"] in (root / "jobs.yaml").read_text()


# ---- and the file says so when it is wrong -----------------------------------
def test_a_tool_written_key_left_in_the_architecture_file_is_refused(tmp_path: Path):
    write_project(tmp_path, "demo")
    path = tmp_path / "qanat.yaml"
    path.write_text(path.read_text() + "\njob_schedules:\n  - {id: x, definition: y, cron: '* * * * *'}\n")
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "which a tool writes" in str(exc.value)
    assert "jobs.yaml" in str(exc.value)
    assert "qanat migrate" in str(exc.value), "the refusal names what to run"


def test_a_part_file_holding_a_key_it_does_not_own_is_refused(tmp_path: Path):
    write_project(tmp_path, "demo")
    (tmp_path / "metrics.yaml").write_text("job_schedules: []\n")
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "does not own" in str(exc.value)


def test_a_part_file_that_is_not_there_is_not_an_error(tmp_path: Path):
    """A project with no metrics has no `metrics.yaml`, and a save that empties
    one removes it rather than leaving a file holding a bare key."""
    from qanat.scaffold import write_project as scaffold

    scaffold(tmp_path, "demo")
    assert (tmp_path / "metrics.yaml").is_file()
    (tmp_path / "metrics.yaml").unlink()

    project, root = load(tmp_path)
    assert project.metrics == []

    save_project(project, root)
    assert not (root / "metrics.yaml").exists(), "nothing to write, so no file"
    assert (root / "jobs.yaml").is_file()
