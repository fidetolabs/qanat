"""The picture `qanat graph` draws, held against the graph it is drawn from."""

from __future__ import annotations

import re
from pathlib import Path

from qanat.graph import UNICODE, Ink, draw, model, render

ESC = re.compile(r"\033\[[0-9;]*m")


def plain(text: str) -> str:
    return ESC.sub("", text)


def a_graph(stages, tables, edges, jobs=()) -> dict:
    """The shape `api.build_graph` hands over, written by hand."""
    kind = {s[0]: s[1] for s in stages}
    return {
        "project": "t",
        "stages": [{"id": i, "kind": k, "description": "", "tables": []} for i, k in stages],
        "tables": [
            {"ref": ref, "stage": ref.split(".")[0], "stage_kind": kind[ref.split(".")[0]],
             "name": ref.partition(".")[2], "producer": by, "rows": rows, "status": state,
             "updated_at": None, "stale": False}
            for ref, by, rows, state in tables
        ],
        "edges": [{"from": a, "to": b, "step": s} for a, b, s in edges],
        "jobs": [{"id": i, "to": list(to)} for i, to in jobs],
    }


def test_a_features_stage_that_chains_gets_a_column_for_each_link():
    """Only a features stage may read and write itself. Drawn one column per
    stage that edge would have to leave the column and come back into it."""
    g = a_graph(
        [("raw", "raw"), ("features", "features"), ("weights", "weights")],
        [("raw.a", "land", 1, "ok"), ("features.b", "mk_b", 1, "ok"),
         ("features.c", "mk_c", 1, "ok"), ("weights.w", "sorter", 1, "ok")],
        [("raw.a", "features.b", "mk_b"), ("features.b", "features.c", "mk_c"),
         ("features.c", "weights.w", "sorter")],
    )
    m = model(g)
    assert [[n.key for n in col] for col in m.cols] == [
        ["raw.a"], ["features.b"], ["features.c"], ["weights.w"]
    ]
    assert ("features", "features", 1, 2) in m.groups


def test_an_edge_that_skips_a_column_is_given_somewhere_to_pass_through():
    """Drawn straight it would cross whatever boxes stand in the way."""
    g = a_graph(
        [("raw", "raw"), ("features", "features"), ("weights", "weights")],
        [("raw.a", "land", 1, "ok"), ("features.b", "mk_b", 1, "ok"),
         ("features.c", "mk_c", 1, "ok"), ("weights.w", "sorter", 1, "ok")],
        [("raw.a", "features.b", "mk_b"), ("features.b", "features.c", "mk_c"),
         ("raw.a", "features.c", "mk_c"), ("features.c", "weights.w", "sorter")],
    )
    m = model(g)
    passing = [n for n in m.cols[1] if n.dummy]
    assert len(passing) == 1
    assert not any(b == "features.c" for a, b in m.edges if a == "raw.a"), \
        "the long edge should run through the pass-through, not straight over the column"


def test_the_drawing_names_every_table_and_every_step(tmp_path: Path):
    from conftest import write_demo_project as write_project

    from qanat.graph import build_graph
    from qanat.project import load
    from qanat.store import Store

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    text = plain(render(build_graph(store, project, root, None), root,
                        ink=Ink("off"), g=UNICODE, width=None))
    store.close()

    for ref in project.tables():
        assert ref.partition(".")[2] in text, f"{ref} is not in the picture"
    for job in project.jobs:
        assert job.id in text, f"{job.id} is not on any arrow"
    for stage in project.stages:
        assert stage.id in text


def test_every_box_in_a_column_starts_at_the_same_place(tmp_path: Path):
    from conftest import write_demo_project as write_project

    from qanat.graph import build_graph
    from qanat.project import load
    from qanat.store import Store

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    lines = plain(render(build_graph(store, project, root, None), root,
                         ink=Ink("off"), g=UNICODE, width=None)).splitlines()
    store.close()

    tops = {mt.start() for ln in lines for mt in re.finditer("╭─", ln)}
    bottoms = {mt.start() for ln in lines for mt in re.finditer("╰─", ln)}
    assert tops == bottoms
    assert len(tops) == 4, f"one column per stage, got {sorted(tops)}"


def test_colour_is_the_stage_and_nothing_else():
    g = a_graph(
        [("raw", "raw"), ("second", "whatever-i-called-it")],
        [("raw.a", "land", 1, "ok"), ("second.w", "sorter", 1, "ok")],
        [("raw.a", "second.w", "sorter")],
    )
    m = model(g)
    from qanat.graph import RAW_INK, _place
    _place(m)

    lit = "\n".join(draw(m, UNICODE, Ink("true")))
    assert RAW_INK.lstrip("#") not in lit           # written as rgb, not as hex
    assert "38;2;162;230;93" in lit, "the raw stage keeps its green"
    assert "38;2;127;196;180" in lit, "the stage after it takes the first cycle colour"
    assert "\033" not in "\n".join(draw(m, UNICODE, Ink("off")))


def test_a_stage_whose_kind_is_not_a_known_word_still_gets_a_colour():
    """Colour follows order, because a stage's kind is a name the user chose.

    It was a lookup against five fixed kinds, and those are gone -- so the
    starter project's own `normalized` stage drew in FAINT and never reached the
    legend. Order is in the file; a keyword may not be.
    """
    from qanat.graph import FAINT, _place, legend

    g = a_graph(
        [("raw", "raw"), ("normalized", "normalized")],
        [("raw.documents", "filings", 40, "ok"), ("normalized.facts", "parse", 43104, "ok")],
        [("raw.documents", "normalized.facts", "parse")],
    )
    m = model(g)
    _place(m)

    assert m.inks["normalized"] != FAINT
    assert m.nodes["normalized.facts"].ink == m.inks["normalized"]

    lit = "\n".join(draw(m, UNICODE, Ink("true")) + legend(m, UNICODE, Ink("true")))
    assert "38;2;127;196;180" in lit, "the normalized stage is drawn, not greyed out"
    assert "normalized" in plain(lit), "and the legend names the stages the project has"


def test_it_gives_up_the_step_names_before_it_overflows_the_terminal():
    g = a_graph(
        [("raw", "raw"), ("weights", "weights")],
        [("raw.a", "land", 1, "ok"), ("weights.w", "a_step_with_a_very_long_name", 1, "ok")],
        [("raw.a", "weights.w", "a_step_with_a_very_long_name")],
    )
    wide = plain(render(g, ".", ink=Ink("off"), g=UNICODE, width=None))
    assert "a_step_with_a_very_long_name" in wide

    narrow = plain(render(g, ".", ink=Ink("off"), g=UNICODE, width=40))
    assert "a_step_with_a_very_long_name" not in narrow
    assert "step names hidden" in narrow
    assert max(len(x) for x in narrow.splitlines() if "·" not in x and "/" not in x) <= 40


def test_the_command_is_gone_and_says_so(tmp_path: Path, capsys):
    """`qanat graph` drew a picture for a person out of a thing whose caller is
    a program, and its own markers disagreed with its legend: `○ 4 rows` for a
    table `ls` drew as `●`. It is the one removed command with no replacement,
    and the message says that rather than pointing somewhere."""
    from qanat.cli import MOVED, main

    assert main(["-p", str(tmp_path), "graph"]) == 2
    assert "nothing" in MOVED["graph"]


def test_the_drawing_still_renders_from_the_read_model(tmp_path: Path):
    """The module stays, and this is what holds it honest.

    Nothing in the shipped surface reaches it now that the command is gone.
    Keeping the test means that if anything points at it again -- a status
    page, a `check --picture` -- it still draws the graph it is handed.
    """
    from conftest import write_demo_project as write_project

    from qanat.graph import UNICODE, Ink, build_graph, render
    from qanat.project import load
    from qanat.store import Store

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    try:
        out = plain(render(build_graph(store, project, root, None), root,
                           ink=Ink("off"), g=UNICODE, labels=True))
    finally:
        store.close()
    assert "normalize" in out
    assert "\033" not in out
