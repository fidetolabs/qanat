"""Build a whole qanat project from nothing, run it, and read what it saw.

Everything here goes through the ordinary user path -- write a `qanat.yaml`, point
a source at a file, run the graph, replay it. No internal shortcuts, because a
shortcut is exactly where a real user's problem would hide.

    from lab import data, harness

    root = harness.build(tmp, "h1", data.drift(data.calendar(), {"A": 0.001}),
                         harness.HOLD_ONE.format(symbol="A"))
    p, r, store, rep, results = harness.run(root)
    harness.at(store, p, r, "2024-03-01")
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

NORMALIZE = """SELECT CAST(date AS DATE) AS date, symbol, CAST(close AS DOUBLE) AS close
FROM raw__bars
QUALIFY row_number() OVER (PARTITION BY symbol, CAST(date AS DATE) ORDER BY date DESC) = 1
"""

#: The smallest honest step: hold one name, always. Its answer is arithmetic.
HOLD_ONE = '''import pandas as pd


def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    return pd.DataFrame([{{"symbol": "{symbol}", "weight": 1.0, "as_of": df["date"].max()}}])
'''

#: Equal weight across whatever the universe allows -- the shape that shows what
#: happens when one of those names stops being priced.
EQUAL_WEIGHT = '''import pandas as pd


def run(ctx):
    df = ctx.read("normalized.prices")
    if df.empty:
        return pd.DataFrame(columns=["symbol", "weight", "as_of"])
    syms = sorted(ctx.universe()["symbol"])
    return pd.DataFrame([{"symbol": s, "weight": 1.0 / len(syms), "as_of": df["date"].max()}
                         for s in syms])
'''


def project_yaml(name: str, *, extra_steps: str = "",
                 step_script: str = "steps/sorter.py", **_ignored) -> str:
    lines = [
        f"project: {name}",
        "store: ./data/q.duckdb",
        "universes:",
        "  - {id: u, index: TEST, symbols: ./universes/u.csv}",
        "stages:",
        "  - {id: raw, kind: raw}",
        "  - {id: normalized, kind: normalized}",
        "sources:",
        "  - {id: bars, to: [raw.bars], connector: csv, mode: replace,",
        "     options: {path: ./seed/bars.csv}}",
        "steps:",
        "  - {id: normalize, from: [raw.bars], to: [normalized.prices],",
        "     script: steps/normalize.sql}",
        "  - id: sorter_h",
        "    from: [normalized.prices]",
        "    to: [normalized.probe]",
        f"    script: {step_script}",
        "    universe: u",
        "    options: {reads: normalized.prices}",
    ]
    if extra_steps:
        lines += extra_steps.rstrip("\n").split("\n")
    lines += [
        "time_columns:",
        "  raw.bars: date",
        "  normalized.prices: date",
        "returns:",
        "  table: normalized.prices",
        "  subject_column: symbol",
        "  date_column: date",
        "  price_column: close",
        "metrics:",
        "  - id: px",
        "    version: 1",
        "    sql: |",
        "      SELECT symbol AS subject, close AS value FROM normalized__prices",
        "      WHERE date <= :as_of",
        "      QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY date DESC) = 1",
    ]
    return "\n".join(lines) + "\n"


def build(where: Path, name: str, bars: pd.DataFrame, step_src: str,
          symbols: list[str] | None = None, files: dict[str, str] | None = None,
          **kw) -> Path:
    """A complete project on disk. `where` should be a tmp_path -- nothing here is
    meant to be kept, and a generated project committed to the repo is exhaust."""
    root = Path(where) / name
    if root.exists():
        shutil.rmtree(root)
    (root / "steps").mkdir(parents=True)
    (root / "seed").mkdir()
    (root / "universes").mkdir()
    bars.to_csv(root / "seed/bars.csv", index=False)
    syms = symbols if symbols is not None else sorted(bars["symbol"].unique())
    pd.DataFrame({"symbol": syms}).to_csv(root / "universes/u.csv", index=False)
    (root / "steps/normalize.sql").write_text(NORMALIZE)
    (root / "steps/sorter.py").write_text(step_src)
    for fname, body in (files or {}).items():
        (root / "steps" / fname).write_text(body)
    (root / "qanat.yaml").write_text(project_yaml(name, **kw))
    return root


def run(root: Path):
    """Load, check and run one pass. Returns (project, root, store, results)."""
    from qanat.project import load, validate
    from qanat.runner import run_all
    from qanat.store import Store

    project, r = load(root)
    report = validate(project, r)
    store = Store(project.store_url(r))
    return project, r, store, report, run_all(store, project, r)


def at(store, project, root, as_of: str):
    """One pass with the clock set to `as_of`. Returns the run results.

    This is how a leak is caught now. The old way was to price a basket and
    look at the money: a step that could see tomorrow earned 1292% against an
    honest 3%. That worked, and it only worked when the leak happened to be
    profitable. Reading the newest row a step could reach is the same question
    asked directly.
    """
    from qanat.runner import run_all

    store.open_pit(as_of, project.time_columns)
    try:
        return run_all(store, project, root, as_of=as_of, sources=False)
    finally:
        store.close_pit()
