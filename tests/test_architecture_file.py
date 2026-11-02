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


def test_definitions_and_schedules_are_tool_written():
    for key in ("ingestion_job_definitions", "transform_job_definitions",
                "job_schedules"):
        assert part_for(key), key


def test_a_metric_is_not_a_key_a_part_file_owns():
    """It used to be `metrics:` in `metrics.yaml`, a list of formula objects each
    with its own `version:` integer. **A metric is a transform writing its own
    table now**, so the definitions in `jobs.yaml` are the whole record -- and
    the metrics stage is the registry, so nothing is kept beside them."""
    from qanat.project_io import RETIRED_PARTS

    assert part_for("metrics") is None
    assert "metrics.yaml" in RETIRED_PARTS


def test_a_universe_is_a_file_and_not_a_key():
    """It used to be both: a csv, and a `universes:` entry in the project read by
    `ctx.universe()` inside a transform. The entry went with the stages that
    needed a basket, so there is nothing left for a part file to own."""
    assert part_for("universes") is None
    from qanat.project_io import ARCHITECTURE

    assert "universes" not in ARCHITECTURE


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
                       sql="SELECT symbol AS subject, date AS date, close AS value "
                           "FROM normalized__prices")
    finally:
        store.close()

    assert (root / "qanat.yaml").read_text() == before
    # It lands in `jobs.yaml` as a transform, because a metric is one -- there
    # is no `metrics.yaml` for it to go in any more.
    assert "spread" in (root / "jobs.yaml").read_text()
    assert not (root / "metrics.yaml").exists()


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
    (tmp_path / "jobs.yaml").write_text("corpus: {}\n")
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "does not own" in str(exc.value)


def test_a_part_file_that_is_not_there_is_not_an_error(tmp_path: Path):
    """A fresh project has `jobs.yaml`; a project with nothing tool-written has
    no part file at all, and loading it is not an error."""
    from qanat.scaffold import write_project as scaffold

    scaffold(tmp_path, "demo")
    assert (tmp_path / "jobs.yaml").is_file()
    (tmp_path / "jobs.yaml").unlink()

    project, root = load(tmp_path)
    assert project.transform_job_definitions == []
    assert project.metric_ids() == []

    save_project(project, root)

def test_a_block_between_two_keys_stays_with_the_key_it_introduces(tmp_path: Path):
    """**Found by using the `check` project.** One `define_metric` appended a
    generated transform to `jobs.yaml` and left the paragraph explaining
    `job_schedules:` above it, where it now introduced the wrong thing.

    ruamel stores a block between two keys on the *tail of the previous value*,
    wherever that tail happens to end -- so a list that grows strands it inside
    itself. It is stored against the key it introduces instead, which is where
    it belonged all along and somewhere growing cannot reach.
    """
    import yaml as _yaml

    from qanat.project_io import render_project

    shapes = {
        "ends in a list": "- id: a\n  key:\n  - one\n",
        "ends in a nested map": "- id: a\n  options:\n    reads: x\n",
        "ends in a scalar with an inline note": "- id: a\n  x: 1  # about a\n",
    }
    #: With and without the blank line. **A block before a sequence item arrives
    #: with no blank line in front of it**, which is the shape the `check`
    #: project produced -- and a split that looked for one missed it.
    for gap in ("\n", ""):
        for label, body in shapes.items():
            path = tmp_path / f"{abs(hash(label + gap))}.yaml"
            path.write_text(
                f"things:\n{body}{gap}# introduces other\n#\n# and a second line\n"
                f"other: 1\n")
            data = _yaml.safe_load(path.read_text())
            data["things"].append({"id": "c", "x": 3})

            out = render_project(data, path)
            where = f"{label} gap={gap!r}"
            assert _yaml.safe_load(out) == data, where
            before, _, after = out.partition("# introduces other")
            assert "other: 1" in after, f"{where}: the block stopped introducing `other`"
            assert "id: c" in before, f"{where}: the entry landed after the block"
            assert "# and a second line" in after, f"{where}: the block lost a line"
            assert "# \n" not in out, f"{where}: a blank line became an empty comment"


def test_an_inline_note_stays_on_the_entry_it_annotates(tmp_path: Path):
    """The other half of the split: a block is separated by a blank line, an
    inline note is not, and only the block travels."""
    import yaml as _yaml

    from qanat.project_io import render_project

    path = tmp_path / "p.yaml"
    path.write_text(
        "things:\n- id: a\n  x: 1  # about a\n\n# introduces other\nother: 1\n")
    data = _yaml.safe_load(path.read_text())
    data["things"].append({"id": "c", "x": 3})

    out = render_project(data, path)
    assert "x: 1  # about a" in out, "the note about `a` stayed on `a`"
    assert out.index("# about a") < out.index("id: c")
