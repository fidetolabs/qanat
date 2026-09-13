"""The terminal console: what it draws, and what it does when you type at it."""

from __future__ import annotations

from pathlib import Path

import pytest

from qanat import chart
from qanat.graph import UNICODE, Ink
from qanat.project import load
from qanat.scaffold import add_shelf_alphas, write_project
from qanat.store import Store
from qanat.tui import CHARTS, App, Screen, read_book, series


@pytest.fixture
def app(tmp_path: Path):
    write_project(tmp_path, "demo")
    add_shelf_alphas(tmp_path)          # four alphas, so the book has rows to move between
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    a = App(object(), store, project, root, Ink("off"), UNICODE)
    a.reload()
    yield a
    store.close()


def a_report(nets: list[float]) -> dict:
    return {
        "run_id": 7,
        "conditions": {"from": "2025-01-01", "to": "2025-12-31", "rebalance": "5d", "seed": 0},
        "totals": {"periods": len(nets), "gross": 0.3, "fees": 0.01, "slippage": 0.02,
                   "net": 0.27, "turnover": 12.5, "hit_rate": 0.48},
        "segments": {"split": "2025-07-01",
                     "in_sample": {"net": 0.2, "periods": len(nets) // 2},
                     "out_of_sample": {"net": 0.07, "periods": len(nets) // 2}},
        "periods": [{"as_of": f"2025-{i % 12 + 1:02d}-01", "net": n, "turnover": abs(n) * 9,
                     "holdings": 4, "gross": n} for i, n in enumerate(nets)],
    }


# ------------------------------------------------------------------ the layout
@pytest.mark.parametrize("size", [(40, 12), (60, 16), (80, 24), (100, 30), (120, 40), (200, 60)])
def test_every_pane_stays_inside_the_terminal(app, size):
    """A frame one column too wide wraps, and a wrapped frame scrolls the screen
    it is trying to hold still."""
    w, h = size
    app.rows[0].report = a_report([0.01, -0.02, 0.03] * 9)
    app.rows[0].loaded = True
    for mode in ("graph", "result"):
        app.mode = mode
        for k in range(len(CHARTS)):
            app.chart = k
            lines = app.frame(w, h)
            assert len(lines) <= h, f"{mode} at {size} drew {len(lines)} rows"
            assert all(len(x) <= w for x in lines), f"{mode} at {size} overran {w} columns"


def test_the_first_frame_is_the_graph_and_the_book(app):
    text = "\n".join(app.frame(120, 40))
    assert "daily_prices" in text and "normalize" in text      # the DAG
    assert "alpha" in text and "curve" in text                 # the book's headings
    assert "never priced" in text                              # nothing has run yet
    assert "chart" in text


def test_a_result_replaces_the_graph_but_not_the_book(app):
    row = app.rows[0]
    row.report, row.loaded = a_report([0.01] * 20), True
    graph = "\n".join(app.frame(120, 40))
    app.mode = "result"
    result = "\n".join(app.frame(120, 40))
    assert "daily_prices" in graph and "daily_prices" not in result
    assert "turnover 12.50" in result and "hit 48.0%" in result
    assert "in sample" in result and "out of sample" in result
    for r in app.rows:                       # the book is in both
        assert r.name in graph and r.name in result


# -------------------------------------------------------------------- the book
def test_the_book_holds_what_the_file_declares_and_what_history_priced(app):
    names = {r.key for r in read_book(app.store, app.project)}
    assert names == {a for a, _ in app.project.alphas}
    assert all(r.run_id is None for r in app.rows), "nothing has been priced yet"


def test_a_blend_is_in_the_book_even_though_nothing_declares_it(app, monkeypatch):
    monkeypatch.setattr(app.store, "alpha_book", lambda: [
        {"alpha": "alpha_a+alpha_b", "runs": 2, "last_run_id": 11, "last_net": 0.5,
         "last_run": "", "best_net": 0.5},
    ])
    rows = read_book(app.store, app.project)
    blend = next(r for r in rows if not r.declared)
    assert blend.key == "alpha_a+alpha_b"
    assert blend.name == "a + b"
    assert rows[0] is blend, "the best net sorts to the top"


# ------------------------------------------------------------------ the charts
def test_equity_compounds_and_drawdown_never_goes_above_zero():
    periods = a_report([0.10, -0.05, 0.10])["periods"]
    eq = series(periods, "equity")
    assert eq[0] == pytest.approx(0.10)
    assert eq[-1] == pytest.approx(1.10 * 0.95 * 1.10 - 1)
    dd = series(periods, "drawdown")
    assert max(dd) <= 0
    assert dd[1] == pytest.approx(-0.05)


def test_a_chart_fills_its_box_whichever_way_it_has_to_stretch():
    """81 periods used to be plotted one per column and fill a third of the box."""
    assert len(chart.fit([1.0, 2.0, 3.0], 9, smooth=True)) == 9
    assert len(chart.fit(list(range(400)), 60)) == 60
    stretched = chart.fit([0.0, 1.0], 5, smooth=True)
    assert stretched == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])
    held = chart.fit([0.0, 1.0], 4)
    assert set(held) == {0.0, 1.0}, "bars are held, not interpolated -- half a rebalance is nothing"
    assert chart.fit([], 10) == [] and chart.fit([5.0], 3) == [5.0, 5.0, 5.0]


def test_downsampling_averages_rather_than_skipping():
    assert chart.fit([0.0, 10.0, 0.0, 10.0], 2) == [5.0, 5.0]


# --------------------------------------------------------------------- the keys
def keyboard(text: str) -> list[str]:
    sc = object.__new__(Screen)
    sc._buf = text
    return [sc._take() for _ in range(len(text)) if sc._buf]


def test_arrow_keys_and_vim_keys_are_the_same_keys():
    assert keyboard("\033[A\033[B\033[C\033[D") == ["up", "down", "right", "left"]
    assert keyboard("\033[5~\033[6~") == ["pgup", "pgdn"]
    assert keyboard("jk\r\033") == ["j", "k", "enter", "esc"]


def test_a_terminal_that_went_away_ends_the_loop(app):
    """select calls a closed fd readable for ever, so EOF read as 'nothing typed'
    spins the loop at a full core."""
    assert not app.quitting
    app.on_key("eof")
    assert app.quitting


def test_moving_and_cycling(app):
    app.rows[0].report, app.rows[0].loaded = a_report([0.01] * 5), True
    app.on_key("enter")
    assert app.mode == "result"
    app.on_key("l")
    assert app.chart == 1
    app.on_key("h")
    app.on_key("h")
    assert app.chart == len(CHARTS) - 1, "the selector wraps"
    app.on_key("g")
    assert app.mode == "graph"
    app.on_key("l")
    assert app.scroll > 0 and app.chart == len(CHARTS) - 1, "in the graph, l pans"
    app.on_key("j")
    assert app.cur == 1
    app.on_key("end")
    assert app.cur == len(app.rows) - 1


def test_enter_on_something_never_priced_offers_to_run_it(app):
    app.project.backtest = None                     # no `backtest:` block
    app.on_key("enter")
    assert app.live is None and "backtest" in app.msg


def test_a_row_with_no_report_says_so_instead_of_drawing_an_empty_chart(app):
    app.mode = "result"
    app.rows[0].loaded = True
    text = "\n".join(app.frame(100, 30))
    assert "never priced" in text
