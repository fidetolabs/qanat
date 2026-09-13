"""The pipeline, drawn where you already are.

`qanat serve` paints this graph on a canvas. This paints the same graph on a
character grid, off the same read model -- `api.build_graph` -- so a table that
says 41,402 rows here is the count the console shows, not a second opinion.

**The table is the node and the step is the edge**, as on screen, and the only
thing a node's colour says is which stage it belongs to. Those four colours are
the ones in `console/dag.js`; they are written out again here because a terminal
cannot read a stylesheet, and they are the one thing in this file that has to be
changed in two places at once.

Columns are layers, not stages. A features stage is allowed to chain -- one step
reading `features.a` and writing `features.b` -- and drawn a column per stage
that edge would have to leave the column and come back into it. So a stage owns
as many columns as its longest chain, and the rule across the top says which
columns those are.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------- the key
#: the stage a table belongs to, which is the only thing its colour says.
#: Kept in step with `console/dag.js` by hand.
STAGE_INK = {
    "raw": "#a2e65d",
    "features": "#7fc4b4",
    "weights": "#e8c069",
    "pnl": "#c2b6d8",
}
#: what the job that writes a table did the last time it ran
STATE_INK = {
    "ok": "#8fce6a",
    "running": "#a2e65d",
    "failed": "#c1503f",
    "idle": "#8b857a",
    "orphan": "#e8c069",
}
FAINT = "#8b857a"
RULE = "#5c5953"
GOLD = "#e8c069"


@dataclass(frozen=True)
class Glyphs:
    h: str
    v: str
    tl: str
    tr: str
    bl: str
    br: str
    arrow: str
    swatch: str
    marks: dict[str, str]
    joins: dict[int, str]


_U, _R, _D, _L = 1, 2, 4, 8

UNICODE = Glyphs(
    h="─", v="│", tl="╭", tr="╮", bl="╰", br="╯", arrow="▸", swatch="■",
    marks={"ok": "●", "running": "◐", "failed": "✕", "idle": "○", "orphan": "⚠"},
    joins={
        _R: "─", _L: "─", _R | _L: "─",
        _U: "│", _D: "│", _U | _D: "│",
        _R | _D: "┌", _D | _L: "┐", _U | _R: "└", _U | _L: "┘",
        _U | _R | _D: "├", _U | _D | _L: "┤", _R | _D | _L: "┬", _U | _R | _L: "┴",
        _U | _R | _D | _L: "┼",
    },
)

ASCII = Glyphs(
    h="-", v="|", tl="+", tr="+", bl="+", br="+", arrow=">", swatch="#",
    marks={"ok": "*", "running": "@", "failed": "x", "idle": "o", "orphan": "!"},
    joins={
        _R: "-", _L: "-", _R | _L: "-",
        _U: "|", _D: "|", _U | _D: "|",
    },
)


def glyphs_for(stream: Any, force_ascii: bool = False) -> Glyphs:
    """Box drawing, unless the terminal cannot spell it."""
    if force_ascii:
        return ASCII
    enc = getattr(stream, "encoding", None) or ""
    try:
        "─│╭╮╰╯▸■●○◐✕⚠┌┐└┘├┤┬┴┼".encode(enc or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return ASCII
    return UNICODE


# ----------------------------------------------------------------- colour
def _rgb(colour: str) -> tuple[int, int, int]:
    s = colour.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _x256(r: int, g: int, b: int) -> int:
    """The nearest of the 256 colours a terminal from before 2016 can name."""
    if abs(r - g) < 12 and abs(g - b) < 12 and abs(r - b) < 12:
        if r < 8:
            return 16
        if r > 248:
            return 231
        return 232 + round((r - 8) / 247 * 23)
    return 16 + 36 * round(r / 255 * 5) + 6 * round(g / 255 * 5) + round(b / 255 * 5)


class Ink:
    """How much colour this terminal can take. `off` makes every call empty."""

    def __init__(self, mode: str = "off") -> None:
        self.mode = mode

    @property
    def on(self) -> bool:
        return self.mode != "off"

    def of(self, colour: str | None = None, *, dim: bool = False, bold: bool = False) -> str:
        if self.mode == "off":
            return ""
        parts: list[str] = []
        if bold:
            parts.append("1")
        if dim:
            parts.append("2")
        if colour:
            r, g, b = _rgb(colour)
            parts += (["38", "2", str(r), str(g), str(b)] if self.mode == "true"
                      else ["38", "5", str(_x256(r, g, b))])
        return f"\033[{';'.join(parts)}m" if parts else ""


def ink_for(when: str, stream: Any) -> Ink:
    """`always`, `never`, or read the room."""
    if when == "never":
        return Ink("off")
    if when != "always":
        if os.environ.get("NO_COLOR"):
            return Ink("off")
        if not getattr(stream, "isatty", lambda: False)():
            return Ink("off")
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return Ink("true")
    term = os.environ.get("TERM", "")
    if term in ("", "dumb"):
        return Ink("true" if when == "always" else "off")
    return Ink("256")


# ----------------------------------------------------------------- canvas
class Canvas:
    """A grid of characters, each with the escape sequence that colours it.

    Lines merge where they meet: drawing a vertical through a horizontal leaves
    a cross, not whichever one was drawn second.
    """

    def __init__(self, w: int, h: int, g: Glyphs) -> None:
        self.w, self.h, self.g = w, h, g
        self.ch = [[" "] * w for _ in range(h)]
        self.st = [[""] * w for _ in range(h)]
        #: which way the line in a cell runs. Kept beside the grid rather than
        #: read back off the character, because `│` is both "up" and "up-down"
        #: and a join that guessed wrong grew a stub out of the end of an arrow.
        self.bt = [[0] * w for _ in range(h)]

    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.w and 0 <= y < self.h

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        for i, c in enumerate(text):
            if self._inside(x + i, y):
                self.ch[y][x + i] = c
                self.st[y][x + i] = style
                self.bt[y][x + i] = 0

    def _join(self, x: int, y: int, bits: int, style: str) -> None:
        if not bits or not self._inside(x, y):
            return
        if self.ch[y][x] != " " and not self.bt[y][x]:
            return                      # something was written here, and writing wins
        merged = self.bt[y][x] | bits
        self.bt[y][x] = merged
        self.ch[y][x] = self.g.joins.get(merged, self.g.joins.get(_U | _R | _D | _L, "+"))
        self.st[y][x] = style

    def hline(self, x0: int, x1: int, y: int, style: str = "") -> None:
        if x0 > x1:
            x0, x1 = x1, x0
        for x in range(x0, x1 + 1):
            self._join(x, y, (_R if x < x1 else 0) | (_L if x > x0 else 0), style)

    def vline(self, y0: int, y1: int, x: int, style: str = "") -> None:
        if y0 > y1:
            y0, y1 = y1, y0
        for y in range(y0, y1 + 1):
            self._join(x, y, (_D if y < y1 else 0) | (_U if y > y0 else 0), style)

    def lines(self) -> list[str]:
        out: list[str] = []
        for row, styles in zip(self.ch, self.st):
            end = len(row)
            while end and row[end - 1] == " ":
                end -= 1
            buf, cur = [], ""
            for i in range(end):
                st = styles[i]
                if st != cur:
                    if cur:
                        buf.append("\033[0m")
                    buf.append(st)
                    cur = st
                buf.append(row[i])
            if cur:
                buf.append("\033[0m")
            out.append("".join(buf))
        while out and not out[-1]:
            out.pop()
        return out


# ------------------------------------------------------------------ model
@dataclass
class Node:
    key: str
    name: str = ""
    stage: str = ""
    kind: str = "features"
    state: str = "idle"
    rows: int = 0
    stale: bool = False
    via: str = ""            # the job that writes it, drawn on the incoming edge
    note: str = ""           # what goes on the bottom rule
    seq: int = 0
    dummy: bool = False
    col: int = 0
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 3

    @property
    def cy(self) -> int:
        return self.y if self.dummy else self.y + 1

    @property
    def right(self) -> int:
        return self.x + self.w


@dataclass
class Model:
    nodes: dict[str, Node]
    cols: list[list[Node]]
    edges: list[tuple[str, str]]
    groups: list[tuple[str, str, int, int]] = field(default_factory=list)  # id, kind, col0, col1


def _ago(ts: str | None) -> str:
    """When the job that writes this table last finished, said the short way.

    The store sets `TimeZone='UTC'` on every connection, so a stamp that comes
    back without an offset is UTC and not local -- read it as local and a table
    written a minute ago says nine hours in Seoul.
    """
    if not ts:
        return ""
    try:
        t = datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00"))
    except ValueError:
        return str(ts)[:10]
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    s = (datetime.now(timezone.utc) - t).total_seconds()
    for n, u in ((31_536_000, "y"), (2_592_000, "mo"), (86_400, "d"), (3_600, "h"), (60, "m")):
        if s >= n:
            return f"{int(s // n)}{u} ago"
    return "just now"


def _rows_text(n: Node) -> str:
    if n.state == "failed":
        return "failed"
    if n.rows:
        return f"{n.rows:,} rows"
    return "not written" if n.state == "idle" else "empty"


def model(g: dict[str, Any]) -> Model:
    """The graph the console draws, laid out in layers instead of pixels."""
    stages = g.get("stages", [])
    order = {s["id"]: i for i, s in enumerate(stages)}
    weights_stage = next((s["id"] for s in stages if s["kind"] == "weights"), None)
    writes = {j["id"]: list(j.get("to") or []) for j in g.get("jobs", [])}

    nodes: dict[str, Node] = {}
    for i, t in enumerate(g.get("tables", [])):
        nodes[t["ref"]] = Node(
            key=t["ref"],
            name=t["name"],
            stage=t["stage"],
            kind=t.get("stage_kind") or "features",
            state=t.get("status") or "idle",
            rows=int(t.get("rows") or 0),
            stale=bool(t.get("stale")),
            via=("backtest" if t.get("written_by_replay") else (t.get("producer") or "")),
            note=_ago(t.get("updated_at")),
            seq=i,
        )

    edges = [(e["from"], e["to"]) for e in g.get("edges", [])
             if e["from"] in nodes and e["to"] in nodes]

    # A replay writes the PnL tables, so no step declares that edge -- but the
    # arrow is real, and without it every PnL table floats unattached.
    for t in g.get("tables", []):
        if not t.get("written_by_replay"):
            continue
        for maker in (t.get("producers") or ([t["producer"]] if t.get("producer") else [])):
            for ref in writes.get(maker, []):
                if weights_stage and ref.startswith(f"{weights_stage}.") and ref in nodes:
                    edges.append((ref, t["ref"]))

    edges = list(dict.fromkeys(edges))
    parents: dict[str, list[str]] = {k: [] for k in nodes}
    for a, b in edges:
        parents[b].append(a)

    # Layers: a stage's first column is one past the last column of the stage
    # before it, and a step that chains inside a features stage pushes its own
    # target one column further right.
    layer: dict[str, int] = {}
    base = 0
    for s in stages:
        mine = [n for n in nodes.values() if n.stage == s["id"]]
        for n in mine:
            layer[n.key] = base
        for _ in range(len(mine) + 1):
            moved = False
            for n in mine:
                want = max([base] + [layer[p] + 1 for p in parents[n.key] if p in layer])
                if want != layer[n.key]:
                    layer[n.key], moved = want, True
            if not moved:
                break
        base = (max(layer[n.key] for n in mine) + 1) if mine else base + 1
    for n in nodes.values():
        n.col = layer.get(n.key, order.get(n.stage, 0))

    # An edge that skips a column would have to cross the boxes standing in it,
    # so it gets a one-cell node there to pass through instead.
    routed: list[tuple[str, str]] = []
    for a, b in edges:
        span = nodes[b].col - nodes[a].col
        if span <= 1:
            routed.append((a, b))
            continue
        prev = a
        for c in range(nodes[a].col + 1, nodes[b].col):
            key = f"\x00{a}>{b}@{c}"
            nodes[key] = Node(key=key, dummy=True, h=1, col=c, seq=nodes[a].seq)
            routed.append((prev, key))
            prev = key
        routed.append((prev, b))

    width = max((n.col for n in nodes.values()), default=-1) + 1
    cols: list[list[Node]] = [[] for _ in range(max(width, len(stages)))]
    for n in nodes.values():
        cols[n.col].append(n)

    groups = []
    for s in stages:
        mine = [n.col for n in nodes.values() if n.stage == s["id"]]
        if mine:
            groups.append((s["id"], s["kind"], min(mine), max(mine)))
        else:
            taken = {c for grp in groups for c in range(grp[2], grp[3] + 1)}
            free = next((c for c in range(len(cols)) if c not in taken and not cols[c]), None)
            if free is not None:
                groups.append((s["id"], s["kind"], free, free))
    return Model(nodes, cols, routed, groups)


# ----------------------------------------------------------------- layout
GAP = 1          # blank rows between two boxes in the same column
INDENT = 2       # the left margin every other qanat command prints at
HEAD = 2         # rows taken by the stage rule across the top


def _clip(text: str, room: int, g: Glyphs) -> str:
    if room <= 0:
        return ""
    if len(text) <= room:
        return text
    cut = "…" if g is UNICODE else "."
    return text[: max(0, room - 1)] + cut


def _place(m: Model) -> None:
    """A node sits level with the average of what feeds it, or below the last one.

    One extra rule: a node never lands on the row a pass-through in the column
    before it is using, unless that pass-through is what feeds it. Two lines that
    share a row in a character grid are one line, and a reader is entitled to read
    it as one -- it is worth a blank row not to claim an edge that is not there.
    """
    centre: dict[str, float] = {}
    for k, col in enumerate(m.cols):
        parents: dict[str, list[str]] = {n.key: [] for n in col}
        for a, b in m.edges:
            if b in parents:
                parents[b].append(a)
        crossing = {n.cy: n.key for n in m.cols[k - 1] if k and n.dummy}

        def bary(n: Node, feeds: dict[str, list[str]] = parents) -> float | None:
            seen = [centre[p] for p in feeds[n.key] if p in centre]
            return sum(seen) / len(seen) if seen else None

        order = sorted(col, key=lambda n: (0, bary(n), n.seq) if bary(n) is not None
                       else (1, 0.0, n.seq))
        cursor = 0
        for n in order:
            b = bary(n)
            want = cursor if b is None else round(b) - (0 if n.dummy else 1)
            y = max(cursor, want)
            mine = set(parents[n.key])
            while crossing.get(y + (0 if n.dummy else 1)) not in (None, *mine):
                y += 1
            n.y = y
            cursor = n.y + n.h + GAP
            centre[n.key] = n.cy


def _size(m: Model, g: Glyphs, labels: bool, maxbox: int) -> dict[str, Any]:
    for n in m.nodes.values():
        if n.dummy:
            continue
        n.w = min(maxbox, max(10, len(n.name) + 5, len(_rows_text(n)) + 6, len(n.note) + 5))

    colw = []
    for k, col in enumerate(m.cols):
        real = [n.w for n in col if not n.dummy]
        floor = max((len(gid) + 2 for gid, _, c0, c1 in m.groups if c0 == c1 == k), default=1)
        colw.append(max(real + [floor]) if (real or floor > 1) else 3)

    lanes: list[dict[str, int]] = []
    labelw: list[int] = []
    for k in range(len(m.cols)):
        out = [n for n in m.cols[k]
               if any(a == n.key and m.nodes[b].col == k + 1 for a, b in m.edges)]
        out.sort(key=lambda n: (n.cy, n.seq))
        lanes.append({n.key: i for i, n in enumerate(out)})
        land = [m.nodes[b] for a, b in m.edges
                if m.nodes[a].col == k and not m.nodes[b].dummy]
        labelw.append(max((len(x.via) for x in land if x.via), default=0) if labels else 0)

    gw = []
    for k in range(len(m.cols) - 1):
        n = len(lanes[k])
        gw.append(n + labelw[k] + 5 if labelw[k] else n + 3)

    x = INDENT
    gx = []
    for k, col in enumerate(m.cols):
        for n in col:
            n.x, n.w = x, colw[k]
        x += colw[k]
        if k < len(m.cols) - 1:
            gx.append(x)
            x += gw[k]
    return {"width": x, "colw": colw, "lanes": lanes, "labelw": labelw, "gw": gw, "gx": gx}


def _box(cv: Canvas, n: Node, ink: Ink, g: Glyphs) -> None:
    edge = ink.of(STAGE_INK.get(n.kind, FAINT))
    y = n.y + HEAD
    name = _clip(n.name, n.w - 5, g)
    top = g.tl + g.h + " " + name + " "
    cv.put(n.x, y, top + g.h * max(0, n.w - len(top) - 1) + g.tr, edge)
    cv.put(n.x + 3, y, name, ink.of(None, bold=True))

    body = _clip(_rows_text(n), n.w - 6, g)
    cv.put(n.x, y + 1, g.v + " " * (n.w - 2) + g.v, edge)
    cv.put(n.x + 2, y + 1, g.marks.get(n.state, g.marks["idle"]),
           ink.of(STATE_INK.get(n.state, FAINT)))
    cv.put(n.x + 4, y + 1, body, ink.of(FAINT) if n.state == "idle" else "")

    note = _clip("stale" if n.stale else n.note, n.w - 5, g)
    if note:
        low = g.bl + g.h + " " + note + " "
        cv.put(n.x, y + 2, low + g.h * max(0, n.w - len(low) - 1) + g.br, edge)
        cv.put(n.x + 3, y + 2, note, ink.of(GOLD if n.stale else FAINT, dim=not n.stale))
    else:
        cv.put(n.x, y + 2, g.bl + g.h * (n.w - 2) + g.br, edge)


def draw_on(m: Model, g: Glyphs, ink: Ink, *, labels: bool = True, maxbox: int = 30) -> Canvas:
    """The grid the drawing lands on, so a caller with a screen can blit it."""
    dim = ink.of(RULE)
    box = _size(m, g, labels, maxbox)
    height = HEAD + max((n.y + n.h for n in m.nodes.values()), default=1) + 1
    cv = Canvas(box["width"] + 1, height, g)

    for gid, kind, c0, c1 in m.groups:
        x0 = INDENT + sum(box["colw"][:c0]) + sum(box["gw"][:c0])
        x1 = INDENT + sum(box["colw"][: c1 + 1]) + sum(box["gw"][:c1]) - 1
        style = ink.of(STAGE_INK.get(kind, FAINT))
        cv.put(x0, 0, gid, style)
        if x1 > x0 + len(gid):
            cv.hline(x0 + len(gid) + 1, x1, 0, ink.of(STAGE_INK.get(kind, FAINT), dim=True))

    for n in m.nodes.values():
        if n.dummy:
            cv.hline(n.x, n.x + n.w - 1, n.cy + HEAD, dim)
        else:
            _box(cv, n, ink, g)

    for a, b in m.edges:
        A, B = m.nodes[a], m.nodes[b]
        k = A.col
        lane = box["gx"][k] + 1 + box["lanes"][k][a]
        cv.hline(A.right, lane, A.cy + HEAD, dim)
        if A.cy != B.cy:
            cv.vline(A.cy + HEAD, B.cy + HEAD, lane, dim)
        stop = B.x - 1 if B.dummy else box["gx"][k] + box["gw"][k] - 2
        cv.hline(lane, stop, B.cy + HEAD, dim)

    for k in range(len(m.cols) - 1):
        room = box["labelw"][k]
        for n in m.cols[k + 1]:
            if n.dummy or not n.via or not any(b == n.key for _, b in m.edges):
                continue
            arrow = box["gx"][k] + box["gw"][k] - 1
            cv.put(arrow, n.cy + HEAD, g.arrow, dim)
            if room:
                lx = box["gx"][k] + 2 + len(box["lanes"][k])
                cv.put(lx, n.cy + HEAD, " " + _clip(n.via, room, g) + " ", ink.of(FAINT))
    return cv


def draw(m: Model, g: Glyphs, ink: Ink, *, labels: bool = True, maxbox: int = 30) -> list[str]:
    return draw_on(m, g, ink, labels=labels, maxbox=maxbox).lines()


# ------------------------------------------------------------------ print
def legend(m: Model, g: Glyphs, ink: Ink) -> list[str]:
    out = []
    if ink.on:
        stages = "   ".join(
            f"{ink.of(STAGE_INK[k])}{g.swatch} {k}\033[0m"
            for k in ("raw", "features", "weights", "pnl")
            if any(n.kind == k for n in m.nodes.values() if not n.dummy)
        )
        out.append(f"{' ' * INDENT}{stages}")

    seen = {n.state for n in m.nodes.values() if not n.dummy} | {"ok", "idle"}
    words = {"ok": "written", "idle": "not written", "running": "running",
             "failed": "failed", "orphan": "nothing produces it"}
    marks = "   ".join(
        f"{ink.of(STATE_INK[s])}{g.marks[s]}\033[0m {w}" if ink.on else f"{g.marks[s]} {w}"
        for s, w in words.items() if s in seen
    )
    if any(n.stale for n in m.nodes.values()):
        word = f"{ink.of(GOLD)}stale\033[0m" if ink.on else "stale"
        marks += f"   {word} · the step that wrote it has changed since"
    out.append(f"{' ' * INDENT}{marks}")
    return out


def render(
    graph: dict[str, Any],
    root: Any,
    *,
    ink: Ink,
    g: Glyphs,
    width: int | None = None,
    labels: bool = True,
) -> str:
    m = model(graph)
    real = [n for n in m.nodes.values() if not n.dummy]
    head = (f"\n{' ' * INDENT}{ink.of('#a2e65d')}{graph.get('project', '')}\033[0m  "
            f"{ink.of(FAINT, dim=True)}{root}\033[0m\n") if ink.on else \
           f"\n{' ' * INDENT}{graph.get('project', '')}  {root}\n"
    if not real:
        return head + f"\n{' ' * INDENT}this project has no tables yet.\n"

    _place(m)
    body, dropped = [], False
    for want_labels, maxbox in ((labels, 30), (labels, 20), (False, 20)):
        body = draw(m, g, ink, labels=want_labels, maxbox=maxbox)
        dropped = labels and not want_labels
        widest = max((len(_plain(x)) for x in body), default=0)
        if not width or widest <= width:
            break

    rows = sum(n.rows for n in real)
    tail = (f"{len(graph.get('stages', []))} stages · {len(real)} tables · {rows:,} rows · "
            f"{len(graph.get('jobs', []))} jobs")
    if dropped:
        tail += " · step names hidden, the terminal is too narrow"
    return "\n".join([head, *body, "", *legend(m, g, ink), "",
                      f"{' ' * INDENT}{ink.of(FAINT, dim=True)}{tail}" + ("\033[0m" if ink.on else ""),
                      ""])


def _plain(s: str) -> str:
    out, skip = [], False
    for ch in s:
        if ch == "\033":
            skip = True
        elif skip:
            skip = ch != "m"
        else:
            out.append(ch)
    return "".join(out)
