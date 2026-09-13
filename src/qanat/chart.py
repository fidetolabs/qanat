"""Curves and bars on a character grid.

The console draws an SVG. A terminal cell is bigger than a pixel, so the trick is
to put more than one dot in it: a braille cell is a 2x4 grid, which makes a 60x8
box of them a 120x32 plot. That is enough for an equity curve over a few hundred
rebalances to keep its shape.

Everything here draws into a `graph.Canvas`, so a chart and the DAG are the same
kind of object and can share a screen.
"""

from __future__ import annotations

from qanat.graph import Canvas, Glyphs

#: which bit of a braille cell is the dot at (column, row) of its own 2x4 grid
DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))
BRAILLE = 0x2800

BLOCKS = "▁▂▃▄▅▆▇█"
ASCII_BLOCKS = "._-~=+*#"


def fit(values: list[float], n: int, *, smooth: bool = False) -> list[float]:
    """Exactly `n` points, however many there were.

    Too many: average into buckets. A 418-rebalance run does not fit in 120
    columns, and taking every fourth point drops the spikes -- which on a drawdown
    curve are the whole story. Averaging keeps the level.

    Too few: stretch. 81 periods plotted one per braille column filled the left
    third of the box and left the rest blank. A curve is interpolated (`smooth`);
    bars are held, because a bar stands for one rebalance and half a rebalance is
    not a number.
    """
    if n <= 0 or not values:
        return []
    m = len(values)
    if m == n:
        return list(values)
    if m > n:
        out = []
        for i in range(n):
            a = int(i * m / n)
            b = max(a + 1, int((i + 1) * m / n))
            chunk = values[a:b]
            out.append(sum(chunk) / len(chunk))
        return out
    if m == 1:
        return [values[0]] * n
    if not smooth:
        return [values[min(m - 1, i * m // n)] for i in range(n)]
    out = []
    for i in range(n):
        t = i * (m - 1) / (n - 1)
        a = int(t)
        b = min(a + 1, m - 1)
        out.append(values[a] * (1 - (t - a)) + values[b] * (t - a))
    return out


def resample(values: list[float], n: int) -> list[float]:
    return fit(values, n)


def span(values: list[float], *, zero: bool = False) -> tuple[float, float]:
    if not values:
        return (0.0, 1.0)
    lo, hi = min(values), max(values)
    if zero:
        lo, hi = min(lo, 0.0), max(hi, 0.0)
    if hi - lo < 1e-12:
        pad = abs(hi) * 0.05 or 1e-6
        lo, hi = lo - pad, hi + pad
    return lo, hi


def sparkline(values: list[float], width: int, g: Glyphs | None = None) -> str:
    blocks = ASCII_BLOCKS if (g and g.h == "-") else BLOCKS
    vals = fit(values, width)
    if not vals:
        return ""
    lo, hi = span(vals)
    return "".join(blocks[min(len(blocks) - 1, int((v - lo) / (hi - lo) * len(blocks)))]
                   for v in vals)


def line(
    cv: Canvas, x: int, y: int, w: int, h: int, values: list[float], style: str = "",
    *, lo: float | None = None, hi: float | None = None, ascii_only: bool = False,
) -> tuple[float, float]:
    """A curve in a w x h box. Returns the range it was scaled to."""
    if w < 1 or h < 1 or not values:
        return (0.0, 1.0)
    across = w if ascii_only else w * 2
    down = h if ascii_only else h * 4
    vals = fit(values, across, smooth=True)
    a, b = span(vals)
    a = a if lo is None else lo
    b = b if hi is None else hi
    if b - a < 1e-12:
        b = a + 1e-9

    def row(v: float) -> int:
        return max(0, min(down - 1, round((b - v) / (b - a) * (down - 1))))

    if ascii_only:
        prev = None
        for i, v in enumerate(vals):
            p = row(v)
            if prev is not None and abs(p - prev) > 1:
                for q in range(min(p, prev) + 1, max(p, prev)):
                    cv.put(x + i, y + q, "|", style)
            cv.put(x + i, y + p, "*", style)
            prev = p
        return a, b

    cells: dict[tuple[int, int], int] = {}
    prev = None
    for i, v in enumerate(vals):
        p = row(v)
        rows = range(min(p, prev), max(p, prev) + 1) if prev is not None else range(p, p + 1)
        for q in rows:
            key = (x + i // 2, y + q // 4)
            cells[key] = cells.get(key, 0) | DOTS[i % 2][q % 4]
        prev = p
    for (cx, cy), bits in cells.items():
        cv.put(cx, cy, chr(BRAILLE + bits), style)
    return a, b


def bars(
    cv: Canvas, x: int, y: int, w: int, h: int, values: list[float],
    up: str = "", down: str = "", base: str = "", *, ascii_only: bool = False,
) -> float:
    """Signed bars either side of a zero rule. Returns the largest absolute value."""
    if w < 1 or h < 3 or not values:
        return 0.0
    vals = fit(values, w)
    top = max((abs(v) for v in vals), default=0.0) or 1e-12
    mid = y + h // 2
    room = max(1, h // 2)
    block = "#" if ascii_only else "█"
    if 0 <= mid < cv.h:
        for i in range(w):
            if 0 <= x + i < cv.w and cv.ch[mid][x + i] == " ":
                cv.put(x + i, mid, "-" if ascii_only else "─", base)
    for i, v in enumerate(vals):
        if abs(v) <= 1e-15:
            continue
        for k in range(max(1, round(abs(v) / top * room))):
            cv.put(x + i, (mid - 1 - k) if v > 0 else (mid + 1 + k), block,
                   up if v > 0 else down)
    return top


def money(x: float | None, dp: int = 2) -> str:
    return "  —" if x is None else f"{x * 100:+.{dp}f}%"
