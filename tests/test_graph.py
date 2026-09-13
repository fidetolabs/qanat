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
         ("features.c", "mk_c", 1, "ok"), ("weights.w", "alpha", 1, "ok")],
        [("raw.a", "features.b", "mk_b"), ("features.b", "features.c", "mk_c"),
         ("features.c", "weights.w", "alpha")],
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
         ("features.c", "mk_c", 1, "ok"), ("weights.w", "alpha", 1, "ok")],
        [("raw.a", "features.b", "mk_b"), ("features.b", "features.c", "mk_c"),
         ("raw.a", "features.c", "mk_c"), ("features.c", "weights.w", "alpha")],
    )
    m = model(g)
    passing = [n for n in m.cols[1] if n.dummy]
    assert len(passing) == 1
    assert not any(b == "features.c" for a, b in m.edges if a == "raw.a"), \
        "the long edge should run through the pass-through, not straight over the column"


def test_a_replay_written_pnl_table_is_still_attached_to_its_alpha():
    """Nothing declares that edge -- a backtest writes the table -- but it is real."""
    g = a_graph(
        [("weights", "weights"), ("pnl", "pnl")],
        [("weights.momentum", "alpha_momentum", 4, "ok")],
        [],
        [("alpha_momentum", ["weights.momentum"])],
    )
    g["tables"].append({
        "ref": "pnl.momentum", "stage": "pnl", "stage_kind": "pnl", "name": "momentum",
        "producer": "alpha_momentum", "producers": ["alpha_momentum"], "rows": 81,
        "status": "ok", "updated_at": None, "stale": False, "written_by_replay": True,
    })
    m = model(g)
    assert ("weights.momentum", "pnl.momentum") in m.edges
    assert m.nodes["pnl.momentum"].via == "backtest"


def test_the_drawing_names_every_table_and_every_step(tmp_path: Path):
    from qanat.api import build_graph
    from qanat.project import load
    from qanat.scaffold import write_project
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
    from qanat.api import build_graph
    from qanat.project import load
    from qanat.scaffold import write_project
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
    assert len(tops) == 5, f"one column per stage, got {sorted(tops)}"


def test_colour_is_the_stage_and_nothing_else():
    g = a_graph(
        [("raw", "raw"), ("weights", "weights")],
        [("raw.a", "land", 1, "ok"), ("weights.w", "alpha", 1, "ok")],
        [("raw.a", "weights.w", "alpha")],
    )
    m = model(g)
    from qanat.graph import STAGE_INK, _place
    _place(m)

    lit = "\n".join(draw(m, UNICODE, Ink("true")))
    assert STAGE_INK["raw"].lstrip("#") not in lit           # written as rgb, not as hex
    assert "38;2;162;230;93" in lit, "the raw stage keeps its green"
    assert "38;2;232;192;105" in lit, "the weights stage keeps its gold"
    assert "\033" not in "\n".join(draw(m, UNICODE, Ink("off")))


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


def test_the_command_runs(tmp_path: Path, capsys):
    from qanat.cli import main
    from qanat.scaffold import write_project

    write_project(tmp_path, "demo")
    assert main(["-p", str(tmp_path), "graph", "--color", "never"]) == 0
    out = capsys.readouterr().out
    assert "normalize" in out and "\033" not in out

    assert main(["-p", str(tmp_path), "graph", "--color", "never", "--ascii"]) == 0
    assert "╭" not in capsys.readouterr().out
