"""Qanat, live, in the terminal you are already in.

`qanat serve` puts the console in a browser. This puts the same three questions on
a character grid:

    what does the pipeline look like, and what has run
    which alpha is worth anything
    what did that one actually do, period by period

Three panes, top to bottom. The top one holds the DAG until you pick an alpha, and
then holds that alpha's result. The middle one says which chart the result is
showing. The bottom one is every alpha this project declares or has ever priced,
with the last run beside it.

Nothing here computes anything. The DAG is `graph.model`, the numbers are the
report the replay already wrote into the store, and a replay that is running right
now is read out of `progress` -- the same in-memory record the console polls, so
the graph fills in from left to right here for the same reason it does there.
"""

from __future__ import annotations

import io
import json
import os
import select
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qanat import chart, progress
from qanat.graph import (
    FAINT,
    GOLD,
    RULE,
    Canvas,
    Glyphs,
    Ink,
    Model,
    _place,
    draw_on,
    model,
)
from qanat.models import Project
from qanat.store import Store

KEY = "#a2e65d"
CYAN = "#7fc4b4"
RED = "#c1503f"
PLUM = "#c2b6d8"

ALT_ON, ALT_OFF = "\033[?1049h", "\033[?1049l"
HIDE, SHOW = "\033[?25l", "\033[?25h"

#: the charts the selector cycles, and what each one makes out of the periods
CHARTS: list[tuple[str, str]] = [
    ("equity", "equity"),
    ("drawdown", "drawdown"),
    ("period", "per period"),
    ("turnover", "turnover"),
    ("holdings", "holdings"),
]

HELP = ("j/k move   enter open   g graph   h/l chart   r re-run   R reload   q quit")


# ---------------------------------------------------------------- the terminal
_SEQ = {
    "[A": "up", "[B": "down", "[C": "right", "[D": "left", "[H": "home", "[F": "end",
    "OA": "up", "OB": "down", "OC": "right", "OD": "left",
    "[5~": "pgup", "[6~": "pgdn", "[1~": "home", "[4~": "end",
}


class Screen:
    """Raw mode, the alternate screen, and a repaint that only sends what moved."""

    def __init__(self) -> None:
        self.fd = sys.__stdout__.fileno()
        self._saved: Any = None
        self._last: list[str] = []
        self._buf = ""

    # -- lifecycle
    def __enter__(self) -> Screen:  # noqa: PYI034
        import termios
        import tty

        self._saved = termios.tcgetattr(self.fd)
        tty.setraw(self.fd)
        self._w(ALT_ON + HIDE + "\033[2J")
        return self

    def __exit__(self, *_exc: object) -> None:
        import termios

        self._w("\033[0m" + SHOW + ALT_OFF)
        if self._saved is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._saved)

    def _w(self, text: str) -> None:
        try:
            os.write(self.fd, text.encode("utf-8", "replace"))
        except OSError:
            pass

    # -- output
    @property
    def size(self) -> tuple[int, int]:
        import shutil

        w, h = shutil.get_terminal_size((100, 30))
        return max(40, w), max(12, h)

    def paint(self, lines: list[str]) -> None:
        out = []
        for i, row in enumerate(lines):
            if i < len(self._last) and self._last[i] == row:
                continue
            out.append(f"\033[{i + 1};1H\033[2K{row}")
        for i in range(len(lines), len(self._last)):
            out.append(f"\033[{i + 1};1H\033[2K")
        self._last = list(lines)
        if out:
            self._w("".join(out) + "\033[0m")

    def forget(self) -> None:
        """After a resize the old frame is no guide to what is on screen."""
        self._last = []
        self._w("\033[2J")

    # -- input
    def key(self, timeout: float = 0.2) -> str | None:
        """One key, or None if nothing was typed. `eof` when the terminal is gone.

        A closed terminal is not "nothing was typed": select reports the fd ready
        for ever and every read comes back empty, so treating it as idle spins the
        loop at a full core until somebody notices the fan.
        """
        if not self._buf:
            try:
                ready, _, _ = select.select([self.fd], [], [], timeout)
            except OSError:
                return "eof"
            if not ready:
                return None
            try:
                data = os.read(self.fd, 1024)
            except OSError:
                return "eof"
            if not data:
                return "eof"
            self._buf = data.decode("utf-8", "replace")
        return self._take()

    def _take(self) -> str:
        b = self._buf
        if b[0] == "\033":
            for n in (4, 3, 2):
                if b[1:n] in _SEQ:
                    self._buf = b[n:]
                    return _SEQ[b[1:n]]
            self._buf = b[1:]
            return "esc"
        self._buf = b[1:]
        return {"\r": "enter", "\n": "enter", "\t": "tab", "\x03": "ctrl-c",
                "\x7f": "back", " ": "space"}.get(b[0], b[0])


# -------------------------------------------------------------------- the book
@dataclass
class Row:
    """One alpha, and the last thing it was worth."""

    key: str                       # "alpha_momentum", or "alpha_a+alpha_b" for a blend
    name: str
    declared: bool = True
    runs: int = 0
    run_id: int | None = None
    net: float | None = None
    report: dict[str, Any] | None = None
    loaded: bool = False

    @property
    def periods(self) -> list[dict[str, Any]]:
        return (self.report or {}).get("periods", [])

    @property
    def totals(self) -> dict[str, Any]:
        return (self.report or {}).get("totals", {})

    def seg(self, which: str) -> dict[str, Any]:
        return ((self.report or {}).get("segments") or {}).get(which) or {}


def read_book(store: Store, project: Project) -> list[Row]:
    """Declared alphas and priced ones, which are not the same set.

    An alpha is declared in `qanat.yaml`; a blend exists only because somebody
    priced two of them together. Both belong in the list -- one is what the project
    says, the other is what was actually done.
    """
    hist = {str(r["alpha"]): r for r in store.alpha_book()}
    rows: list[Row] = []
    for step_id, ref in project.alphas:
        h = hist.pop(step_id, None)
        rows.append(Row(
            key=step_id, name=Project.alpha_name(step_id, ref), declared=True,
            runs=int(h["runs"]) if h else 0,
            run_id=int(h["last_run_id"]) if h else None,
            net=float(h["last_net"]) if h and h["last_net"] is not None else None,
        ))
    for key, h in hist.items():
        rows.append(Row(
            key=key, name=Project.alpha_name(key), declared=False,
            runs=int(h["runs"]), run_id=int(h["last_run_id"]),
            net=float(h["last_net"]) if h["last_net"] is not None else None,
        ))
    rows.sort(key=lambda r: (r.net is None, -(r.net or 0.0), r.name))
    return rows


def load_report(store: Store, row: Row) -> None:
    row.loaded = True
    if row.run_id is None:
        return
    got = store.backtest(row.run_id)
    if got and got.get("report"):
        try:
            row.report = json.loads(got["report"])
        except (ValueError, TypeError):
            row.report = None


# ------------------------------------------------------------------ the charts
def series(periods: list[dict[str, Any]], kind: str) -> list[float]:
    nets = [float(p.get("net") or 0.0) for p in periods]
    if kind == "equity":
        out, acc = [], 1.0
        for n in nets:
            acc *= 1.0 + n
            out.append(acc - 1.0)
        return out
    if kind == "drawdown":
        out, acc, peak = [], 1.0, 1.0
        for n in nets:
            acc *= 1.0 + n
            peak = max(peak, acc)
            out.append(acc / peak - 1.0)
        return out
    if kind == "period":
        return nets
    if kind == "turnover":
        return [float(p.get("turnover") or 0.0) for p in periods]
    if kind == "holdings":
        return [float(p.get("holdings") or 0.0) for p in periods]
    return nets


def blit(dst: Canvas, src: Canvas, x: int, y: int, w: int, h: int, sx: int = 0) -> None:
    for j in range(h):
        if not (0 <= y + j < dst.h and 0 <= j < src.h):
            continue
        for i in range(w):
            if not (0 <= x + i < dst.w and 0 <= sx + i < src.w):
                continue
            dst.ch[y + j][x + i] = src.ch[j][sx + i]
            dst.st[y + j][x + i] = src.st[j][sx + i]
            dst.bt[y + j][x + i] = src.bt[j][sx + i]


def pct(x: float | None, dp: int = 2) -> str:
    return "—" if x is None else f"{x * 100:+.{dp}f}%"


# --------------------------------------------------------------------- the app
@dataclass
class Live:
    """A replay running in this process, watched through `progress`."""

    key: str
    name: str
    thread: threading.Thread | None = None
    error: str | None = None
    run_id: int | None = None
    done: bool = False
    notes: list[str] = field(default_factory=list)


class App:
    def __init__(self, screen: Screen, store: Store, project: Project, root: Path,
                 ink: Ink, g: Glyphs) -> None:
        self.sc, self.store, self.project, self.root = screen, store, project, root
        self.ink, self.g = ink, g
        self.rows: list[Row] = []
        self.cur = 0
        self.top = 0                   # first book row on screen
        self.chart = 0
        self.mode = "graph"            # graph | result | running
        self.scroll = 0                # the graph is usually wider than the pane
        self.msg = ""
        self.msg_ink = FAINT
        self.live: Live | None = None
        self.quitting = False
        self.abandoned = False
        self.model: Model | None = None
        self.base_state: dict[str, str] = {}
        self.window: tuple[str, str] | None = None
        self._gcache: Canvas | None = None
        self.gdict: dict[str, Any] | None = None
        self.run_model: Model | None = None

    # -- data ------------------------------------------------------------------
    def reload(self) -> None:
        from qanat.api import build_graph

        self.gdict = build_graph(self.store, self.project, self.root, None)
        self.model = model(self.gdict)
        _place(self.model)
        self.base_state = {n.key: n.state for n in self.model.nodes.values()}
        self.rows = read_book(self.store, self.project)
        self.cur = min(self.cur, max(0, len(self.rows) - 1))

    def fill_reports(self) -> None:
        """Every report, in the background, so the first frame is not held up."""
        for row in list(self.rows):
            if self.quitting:
                return
            if not row.loaded:
                try:
                    load_report(self.store, row)
                except Exception:  # noqa: BLE001 -- a bad report must not take the app down
                    row.loaded = True

    def row(self) -> Row | None:
        return self.rows[self.cur] if self.rows else None

    def data_window(self) -> tuple[str, str] | None:
        """The first and last date the prices reach, which is the widest a replay
        can be. Same answer the console's run form starts from."""
        if self.window:
            return self.window
        bt = self.project.backtest
        if bt is None or not self.store.exists(bt.prices):
            return None
        col = self.store.time_column(bt.prices, self.project.time_columns.get(bt.prices))
        if not col:
            return None
        df = self.store.read(bt.prices)
        if df.empty:
            return None
        self.window = (str(df[col].min())[:10], str(df[col].max())[:10])
        return self.window

    # -- running a replay ------------------------------------------------------
    def start(self, row: Row) -> None:
        from qanat.backtest import alpha_ids_of, run_backtest

        if self.live and not self.live.done:
            self.say("a replay is already running", GOLD)
            return
        bt = self.project.backtest
        if bt is None:
            self.say("this project has no `backtest:` block -- nothing to price with", RED)
            return
        span = self.data_window()
        if span is None:
            self.say(f"no prices in {bt.prices} yet. `qanat run` first", RED)
            return

        ids = alpha_ids_of(row.key)
        step = self.project.job(ids[0])
        live = Live(key=row.key, name=row.name)

        def work() -> None:
            try:
                res = run_backtest(
                    self.store, self.project, self.root, span[0], span[1],
                    rebalance=getattr(step, "rebalance", None) or bt.rebalance,
                    decay=getattr(step, "decay", None),
                    split=bt.split or None, alpha=ids, seed=0,
                )
                live.run_id = res.run_id
                live.notes = list(res.notes)
            except Exception as exc:  # noqa: BLE001 -- it belongs on screen, not on stderr
                live.error = f"{type(exc).__name__}: {exc}"
            finally:
                live.done = True

        self.run_model = None
        live.thread = threading.Thread(target=work, daemon=True, name="qanat-tui-replay")
        self.live = live
        self.mode = "running"
        self.say(f"replaying {row.name} · {span[0]} → {span[1]}", KEY)
        live.thread.start()

    def settle(self) -> None:
        """A replay that just finished: take its result and show it."""
        live = self.live
        if live is None or not live.done:
            return
        self.live = None
        if live.error:
            self.mode = "graph"
            self.say(live.error, RED)
            return
        self.reload()
        for i, r in enumerate(self.rows):
            if r.key == live.key:
                self.cur, self.top = i, min(self.top, i)
                load_report(self.store, r)
                break
        self.mode = "result"
        self.say(f"{live.name} priced · run {live.run_id}", KEY)

    def say(self, text: str, colour: str = FAINT) -> None:
        self.msg, self.msg_ink = text, colour

    # -- painting --------------------------------------------------------------
    def frame(self, w: int, h: int) -> list[str]:
        cv = Canvas(w, h, self.g)
        foot = h - 1
        book_h = max(3, min(len(self.rows) + 1, h // 3))
        book_y = foot - book_h
        sel_y = book_y - 2
        pane_y, pane_h = 2, max(3, sel_y - 3)
        self._header(cv, w)
        if self.mode == "running":
            self._pane_running(cv, pane_y, w, pane_h)
        elif self.mode == "result":
            self._pane_result(cv, pane_y, w, pane_h)
        else:
            self._pane_graph(cv, pane_y, w, pane_h)
        self._selector(cv, sel_y, w)
        self._book(cv, book_y, w, book_h)
        self._footer(cv, foot, w)
        return cv.lines()

    def _right(self, cv: Canvas, y: int, w: int, text: str, style: str) -> None:
        cv.put(max(1, w - 1 - len(text)), y, text, style)

    def _rule(self, cv: Canvas, y: int, w: int) -> None:
        cv.hline(1, w - 2, y, self.ink.of(RULE, dim=True))

    def _header(self, cv: Canvas, w: int) -> None:
        i = self.ink
        cv.put(1, 0, "qanat", i.of(KEY, bold=True))
        cv.put(7, 0, self.project.name, i.of(None, bold=True))
        tail = ""
        m = self.model
        if m:
            real = [n for n in m.nodes.values() if not n.dummy]
            tail = (f"{len(m.groups)} stages · {len(real)} tables · "
                    f"{len(self.rows)} alphas")
            self._right(cv, 0, w, tail, i.of(FAINT, dim=True))
        x = 9 + len(self.project.name)
        room = w - x - len(tail) - 3
        if room > 6:
            path = str(self.root)
            cv.put(x, 0, path if len(path) <= room else "…" + path[-(room - 1):],
                   i.of(FAINT, dim=True))

    def _footer(self, cv: Canvas, y: int, w: int) -> None:
        if self.msg:
            cv.put(1, y, self.msg[: w - 2], self.ink.of(self.msg_ink))
        else:
            cv.put(1, y, HELP[: w - 2], self.ink.of(FAINT, dim=True))

    def _selector(self, cv: Canvas, y: int, w: int) -> None:
        i = self.ink
        live = self.mode in ("result", "running")
        cv.put(1, y, "chart", i.of(FAINT, dim=True))
        x = 8
        for k, (_kind, label) in enumerate(CHARTS):
            on = live and k == self.chart
            cv.put(x, y, label, i.of(KEY, bold=True) if on else i.of(FAINT, dim=not live))
            if on:
                cv.hline(x, x + len(label) - 1, y + 1, i.of(KEY))
            x += len(label) + 3
        if not live:
            self._right(cv, y, w, "pick an alpha below", i.of(FAINT, dim=True))

    # -- the graph
    def _graph_canvas(self) -> Canvas | None:
        if self.model is None:
            return None
        return draw_on(self.model, self.g, self.ink, labels=True, maxbox=30)

    def _pane_graph(self, cv: Canvas, y: int, w: int, h: int) -> None:
        g = self._gcache
        if g is None:
            g = self._gcache = self._graph_canvas()
        if g is None:
            return
        self.scroll = max(0, min(self.scroll, max(0, g.w - (w - 2))))
        blit(cv, g, 1, y, w - 2, min(h, g.h), sx=self.scroll)
        if g.w > w - 2:
            self._right(cv, y + h - 1, w,
                        f"{self.scroll}/{g.w - (w - 2)} ← → pans",
                        self.ink.of(FAINT, dim=True))

    def _focus(self, in_run: set[str]) -> Model | None:
        """Just the lineage this replay walks.

        A replay does not re-run the whole project -- it runs the steps upstream of
        one alpha, once per as-of date. Drawing the other alphas beside it wastes
        the rows and implies they are being priced too.
        """
        g = self.gdict
        if not g or not in_run:
            return None
        keep = {t["ref"] for t in g["tables"] if t.get("producer") in in_run}
        keep |= {e["from"] for e in g["edges"] if e["to"] in keep}
        cut = dict(g)
        cut["tables"] = [t for t in g["tables"] if t["ref"] in keep]
        cut["edges"] = [e for e in g["edges"] if e["from"] in keep and e["to"] in keep]
        m = model(cut)
        _place(m)
        return m

    def _light(self, m: Model | None, snap: dict[str, Any]) -> None:
        """Colour the DAG by what this pass has done, not by what the store holds.

        The jobs of one as-of date arrive in dependency order, so lighting them as
        they land is the graph filling in from the left. That is the console's
        trick, and it is the same record underneath.
        """
        if m is None:
            return
        in_run = set(snap.get("jobs_in_run") or [])
        passes = snap.get("passes") or []
        done = {j["job"]: j["status"] for j in (passes[-1]["jobs"] if passes else [])}
        for n in m.nodes.values():
            if n.dummy:
                continue
            if n.via in in_run:
                n.state = {"ok": "ok", "failed": "failed"}.get(done.get(n.via, ""), "idle")
            else:
                n.state = self.base_state.get(n.key, n.state)

    def _pane_running(self, cv: Canvas, y: int, w: int, h: int) -> None:
        i = self.ink
        snap = progress.snapshot()
        in_run = set(snap.get("jobs_in_run") or [])
        if self.run_model is None:
            self.run_model = self._focus(in_run)
        m = self.run_model or self.model
        self._light(m, snap)
        g = draw_on(m, self.g, self.ink, labels=True, maxbox=30) if m else None

        done, total = int(snap.get("stops_done") or 0), int(snap.get("stops_total") or 0)
        periods = snap.get("periods") or []
        curve_h = 5 if h >= 13 and periods else 0
        # the bar and the curve sit on the floor of the pane, so the graph above
        # them does not leave a band of nothing between itself and the numbers
        bar_y = y + max(0, h - 2 - curve_h)
        if g:
            blit(cv, g, 1, y, w - 2, min(bar_y - y, g.h), sx=self.scroll)
        live = self.live
        head = f"replaying {live.name if live else ''}"
        stop = str(snap.get("stop") or "")[:10]
        where = f"{done}/{total}  {stop}" if total else "starting"
        cv.put(1, bar_y, head, i.of(KEY, bold=True))
        cv.put(3 + len(head), bar_y, where, i.of(FAINT))
        left = 5 + len(head) + len(where)
        room = w - 2 - left
        if room >= 10:
            filled = int(room * (done / total)) if total else 0
            cv.put(left, bar_y, ("█" if self.g.h == "─" else "#") * filled, i.of(KEY))
            cv.put(left + filled, bar_y,
                   ("░" if self.g.h == "─" else ".") * (room - filled), i.of(RULE, dim=True))

        totals = snap.get("totals") or {}
        if totals:
            cv.put(1, bar_y + 1,
                   f"net so far {pct(totals.get('net'))}   "
                   f"gross {pct(totals.get('gross'))}   "
                   f"turnover {float(totals.get('turnover') or 0):.2f}   "
                   f"hit {float(totals.get('hit_rate') or 0) * 100:.1f}%",
                   i.of(None))
        if curve_h:
            vals = series(periods, CHARTS[self.chart][0])
            lo, hi = chart.line(cv, 1, bar_y + 2, w - 12, curve_h, vals, i.of(CYAN),
                               ascii_only=self.g.h == "-")
            cv.put(w - 10, bar_y + 2, pct(hi, 1), i.of(FAINT, dim=True))
            cv.put(w - 10, bar_y + 1 + curve_h, pct(lo, 1), i.of(FAINT, dim=True))

    # -- one result
    def _pane_result(self, cv: Canvas, y: int, w: int, h: int) -> None:
        i = self.ink
        row = self.row()
        if row is None:
            cv.put(1, y, "no alphas in this project yet", i.of(FAINT))
            return
        if not row.loaded:
            load_report(self.store, row)
        if not row.report:
            cv.put(1, y, f"{row.name}", i.of(KEY, bold=True))
            cv.put(1, y + 2, "never priced.  enter runs the replay and draws it as it goes",
                   i.of(FAINT))
            return

        rep = row.report
        cond = rep.get("conditions") or {}
        cv.put(1, y, row.name, i.of(KEY, bold=True))
        head = (f"run {rep.get('run_id')} · {cond.get('from')} → {cond.get('to')} "
                f"every {cond.get('rebalance')} · seed {cond.get('seed')}")
        cv.put(2 + len(row.name), y, head[: max(0, w - 4 - len(row.name))], i.of(FAINT, dim=True))

        kind = CHARTS[self.chart][0]
        vals = series(rep.get("periods") or [], kind)
        stats_h = 2 if rep.get("segments", {}).get("split") else 1
        ch = max(2, h - 2 - stats_h)
        if not vals:
            cv.put(1, y + 2, "no periods were priced", i.of(GOLD))
            return
        if kind in ("period", "turnover", "holdings"):
            top = chart.bars(cv, 1, y + 2, w - 12, ch, vals, i.of(KEY), i.of(RED),
                             i.of(RULE, dim=True), ascii_only=self.g.h == "-")
            hi, lo = top, -top if kind == "period" else 0.0
        else:
            lo, hi = chart.line(cv, 1, y + 2, w - 12, ch, vals, i.of(CYAN if kind == "equity" else GOLD),
                                ascii_only=self.g.h == "-")
        fmt = (lambda v: f"{v:,.1f}") if kind == "holdings" else (lambda v: pct(v, 1))
        cv.put(w - 10, y + 2, fmt(hi), i.of(FAINT, dim=True))
        cv.put(w - 10, y + 1 + ch, fmt(lo), i.of(FAINT, dim=True))

        t = rep.get("totals") or {}
        sy = y + 2 + ch
        cv.put(1, sy, (
            f"net {pct(t.get('net'))}   gross {pct(t.get('gross'))}   "
            f"fees {pct(-(t.get('fees') or 0))}   slip {pct(-(t.get('slippage') or 0))}   "
            f"turnover {float(t.get('turnover') or 0):.2f}   "
            f"hit {float(t.get('hit_rate') or 0) * 100:.1f}%   "
            f"{int(t.get('periods') or 0)} periods"
        )[: w - 2], i.of(None))
        seg = rep.get("segments") or {}
        if seg.get("split"):
            ins, out = seg.get("in_sample") or {}, seg.get("out_of_sample") or {}
            cv.put(1, sy + 1, (
                f"in sample {pct(ins.get('net'))} ({int(ins.get('periods') or 0)})   "
                f"out of sample {pct(out.get('net'))} ({int(out.get('periods') or 0)})   "
                f"split {str(seg.get('split'))[:10]}"
            )[: w - 2], i.of(PLUM))

    # -- the book
    def _columns(self, w: int) -> list[tuple[str, int, bool]]:
        """(title, width, right-aligned). Numbers line up under their own heading
        or the column is a lie about which one you are reading."""
        wide = w >= 96
        cols = [("alpha", max(16, w - (78 if wide else 54)), False),
                ("runs", 6, True), ("net", 10, True)]
        if wide:
            cols += [("in samp", 11, True), ("out samp", 11, True)]
        cols += [("turn", 8, True), ("hit", 8, True)]
        used = 3 + sum(c[1] for c in cols)
        cols.append(("curve", max(0, w - used - 2), False))
        return cols

    @staticmethod
    def _cell(cv: Canvas, x: int, width: int, y: int, text: str, style: str,
              right: bool) -> None:
        if width <= 0:
            return
        t = text[:width]
        cv.put(x + (width - len(t) - 1 if right else 0), y, t, style)

    def _book(self, cv: Canvas, y: int, w: int, h: int) -> None:
        i = self.ink
        self._rule(cv, y - 1, w)
        cols = self._columns(w)
        xs, x = [], 3
        for _t, width, _r in cols:
            xs.append(x)
            x += width
        faint = i.of(FAINT, dim=True)
        for (title, width, right), cx in zip(cols, xs):
            self._cell(cv, cx, width, y, title, faint, right)

        seen = h - 1
        self.top = max(0, min(self.top, max(0, len(self.rows) - seen)))
        self.top = min(self.top, self.cur)
        if self.cur >= self.top + seen:
            self.top = self.cur - seen + 1

        for k in range(seen):
            idx = self.top + k
            if idx >= len(self.rows):
                break
            r = self.rows[idx]
            ry, on = y + 1 + k, idx == self.cur
            running = bool(self.live and not self.live.done and self.live.key == r.key)
            if on:
                cv.put(1, ry, self.g.arrow, i.of(KEY, bold=True))

            name = r.name + ("" if r.declared else "  blend")
            self._cell(cv, xs[0], cols[0][1], ry, name,
                       i.of(None, bold=True) if on else i.of(None), False)
            self._cell(cv, xs[1], cols[1][1], ry, str(r.runs) if r.runs else "—", faint, True)
            net_ink = (i.of(FAINT) if running or r.net is None
                       else i.of(KEY if r.net > 0 else RED, bold=on))
            self._cell(cv, xs[2], cols[2][1], ry,
                       "running…" if running else pct(r.net), net_ink, True)

            n = 3
            if len(cols) > 6:
                for which in ("in_sample", "out_of_sample"):
                    seg = r.seg(which)
                    self._cell(cv, xs[n], cols[n][1], ry,
                               pct(seg.get("net")) if seg else "—", i.of(FAINT), True)
                    n += 1
            t = r.totals
            self._cell(cv, xs[n], cols[n][1], ry,
                       f"{float(t['turnover']):.2f}" if t.get("turnover") is not None else "—",
                       i.of(FAINT), True)
            n += 1
            self._cell(cv, xs[n], cols[n][1], ry,
                       f"{float(t['hit_rate']) * 100:.1f}%" if t.get("hit_rate") is not None
                       else "—", i.of(FAINT), True)
            n += 1
            room = cols[n][1]
            if room < 5:
                continue
            if running:
                vals = series(progress.snapshot().get("periods") or [], "equity")
            else:
                vals = series(r.periods, "equity") if r.periods else []
            if vals:
                cv.put(xs[n], ry, chart.sparkline(vals, room, self.g),
                       i.of(KEY if vals[-1] > 0 else RED))
            elif r.run_id is None:
                # no report needed to know this -- the book already says it has
                # never produced a run, and waiting on the read leaves the column
                # blank for the first second the screen is up
                cv.put(xs[n], ry, "never priced"[:room], faint)

    # -- keys ------------------------------------------------------------------
    def move(self, by: int) -> None:
        if not self.rows:
            return
        self.cur = max(0, min(len(self.rows) - 1, self.cur + by))
        if self.mode == "result":
            self.say("")

    def open(self) -> None:
        row = self.row()
        if row is None:
            return
        if not row.loaded:
            load_report(self.store, row)
        if row.report:
            self.mode = "result"
            self.say("")
        else:
            self.start(row)

    def on_key(self, k: str) -> None:
        busy = bool(self.live and not self.live.done)
        if k in ("ctrl-c", "Q", "eof"):
            self.abandoned = bool(self.live and not self.live.done)
            self.quitting = True
        elif k == "q":
            if busy:
                self.say("a replay is running. Q abandons it -- `qanat run` afterwards", GOLD)
            else:
                self.quitting = True
        elif k in ("j", "down"):
            self.move(1)
        elif k in ("k", "up"):
            self.move(-1)
        elif k == "pgdn":
            self.move(10)
        elif k == "pgup":
            self.move(-10)
        elif k == "home":
            self.move(-len(self.rows))
        elif k == "end":
            self.move(len(self.rows))
        elif k == "g":
            if not busy:
                self.mode = "graph"
                self.say("")
        elif k == "enter":
            if not busy:
                self.open()
        elif k in ("l", "right", "tab"):
            if self.mode == "graph":
                self.scroll += 8
            else:
                self.chart = (self.chart + 1) % len(CHARTS)
        elif k in ("h", "left"):
            if self.mode == "graph":
                self.scroll = max(0, self.scroll - 8)
            else:
                self.chart = (self.chart - 1) % len(CHARTS)
        elif k == "r":
            row = self.row()
            if row is not None:
                self.start(row)
        elif k == "R":
            if not busy:
                self._gcache = None
                self.reload()
                self.say("reloaded", KEY)
        elif k in ("?", "esc"):
            self.say(HELP if k == "?" else "")

    # -- the loop --------------------------------------------------------------
    def loop(self) -> None:
        self._gcache = None
        self.reload()
        threading.Thread(target=self.fill_reports, daemon=True, name="qanat-tui-book").start()
        size = None
        while not self.quitting:
            w, h = self.sc.size
            if (w, h) != size:
                self.sc.forget()
                size = (w, h)
            if self.live and self.live.done:
                self.settle()
                self._gcache = None
            self.sc.paint(self.frame(w, h))
            k = self.sc.key(0.1 if self.mode == "running" else 0.25)
            while k:
                self.on_key(k)
                k = self.sc.key(0.0)


def run(project: Project, root: Path, store: Store, ink: Ink, g: Glyphs) -> int:
    """Take the terminal, and give it back whatever happens.

    This owns closing the store, which the caller would otherwise do in a `finally`
    -- and closing it under a replay that is still writing blocks on the store's own
    lock, so `Q` would hang instead of quitting. An abandoned replay leaves the
    connection to the interpreter, which is the same exit a SIGKILL gives it, and
    the same one `_repair_after_crash` already knows how to clean up.
    """
    if not sys.__stdout__ or not sys.__stdout__.isatty():
        print("qanat tui needs a terminal. For a picture you can pipe, use `qanat graph`.",
              file=sys.stderr)
        store.close()
        return 1
    try:
        import termios  # noqa: F401
    except ImportError:
        print("qanat tui needs a POSIX terminal.", file=sys.stderr)
        store.close()
        return 1

    keep, app = sys.stdout, None
    try:
        with Screen() as sc:
            # Anything the engine prints would land in the middle of the drawing.
            # It goes in a bucket instead, and the drawing says what happened.
            sys.stdout = io.StringIO()
            app = App(sc, store, project, root, ink, g)
            try:
                app.loop()
            finally:
                app.quitting = True
    finally:
        sys.stdout = keep

    if app is not None and app.abandoned:
        print("a replay was abandoned part way through a pass. The tables it rewrote "
              "hold the rows from one as-of date -- `qanat run` rebuilds them from raw.",
              file=sys.stderr)
        sys.stderr.flush()
        # The replay is a daemon thread inside DuckDB, which holds no Python lock
        # to interrupt and will not notice the interpreter shutting down until its
        # current statement returns. Waiting for that is exactly what the person
        # pressing Q asked not to do.
        os._exit(130)
    store.close()
    return 0
