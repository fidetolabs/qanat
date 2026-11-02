"""Build a whole qanat project from nothing, run it, and read what it saw.

Everything here goes through the ordinary user path -- write the project files,
point an ingestion job at a file, run the graph, replay it. No internal
shortcuts, because a shortcut is exactly where a real user's problem would hide.

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
    import pathlib

    syms = sorted(pd.read_csv(
        pathlib.Path(ctx.root) / "universes/u.csv")["symbol"])
    return pd.DataFrame([{"symbol": s, "weight": 1.0 / len(syms), "as_of": df["date"].max()}
                         for s in syms])
'''


def project_yaml(name: str, *, extra_steps: str = "",
                 step_script: str = "transforms/sorter.py", **_ignored) -> str:
    """One blob, which `build` then splits into the files a project has.

    The split is `qanat.yaml` against what the tools write, and a harness that
    spelled it out in every call would be describing the file layout rather than
    the project. `qanat migrate` is the one thing that knows the layout.
    """
    lines = [
        f"project: {name}",
        "store: ./data/q.duckdb",
        "stages:",
        "  - {id: raw, kind: raw}",
        "  - {id: normalized, kind: normalized}",
        # The registry. Every table in here is a metric, so `px` below exists
        # because something writes `metrics.px` -- not because a list says so.
        "  - {id: metrics, kind: metrics}",
        "ingestion_job_definitions:",
        "  - {id: bars, to: [raw.bars], connector: csv, key: [symbol, date],",
        "     options: {path: ./seed/bars.csv}}",
        "transform_job_definitions:",
        "  - {id: normalize, from: [raw.bars], to: [normalized.prices],",
        "     script: transforms/normalize.sql}",
        "  - id: sorter_h",
        "    from: [normalized.prices]",
        "    to: [normalized.probe]",
        f"    script: {step_script}",
        "    options: {reads: normalized.prices}",
        # **A metric is a transform filling its own table.** It used to be a
        # `metrics:` entry with a `version:` and `:as_of` in its SQL, which
        # stored nothing -- so a screen had to run the formula, and a formula
        # with no date to bind ran over every row.
        "  - id: px",
        "    from: [normalized.prices]",
        "    to: [metrics.px]",
        "    script: transforms/metrics/px.sql",
        "    key: [subject, date]",
    ]
    if extra_steps:
        lines += extra_steps.rstrip("\n").split("\n")
    lines += [
        "time_columns:",
        "  raw.bars: date",
        "  normalized.prices: date",
        "  metrics.px: date",
        "returns:",
        "  table: normalized.prices",
        "  subject_column: symbol",
        "  date_column: date",
        "  price_column: close",
    ]
    return "\n".join(lines) + "\n"


#: The one metric every battery screens on: the close, as a series.
PX = """\
-- px -- the close, as one number per symbol per day.
--
-- `date` is the day the value became true, so a screen reads the newest row at
-- or before the date it asks about. One shape, both calendars.
SELECT symbol AS subject,
       CAST(date AS DATE) AS date,
       close AS value
FROM normalized__prices
"""


def build(where: Path, name: str, bars: pd.DataFrame, step_src: str,
          symbols: list[str] | None = None, files: dict[str, str] | None = None,
          **kw) -> Path:
    """A complete project on disk. `where` should be a tmp_path -- nothing here is
    meant to be kept, and a generated project committed to the repo is exhaust."""
    from qanat.migrate import apply_migration, plan_migration

    root = Path(where) / name
    if root.exists():
        shutil.rmtree(root)
    (root / "transforms").mkdir(parents=True)
    (root / "seed").mkdir()
    (root / "universes").mkdir()
    bars.to_csv(root / "seed/bars.csv", index=False)
    syms = symbols if symbols is not None else sorted(bars["symbol"].unique())
    pd.DataFrame({"symbol": syms}).to_csv(root / "universes/u.csv", index=False)
    (root / "transforms/normalize.sql").write_text(NORMALIZE)
    (root / "transforms/metrics").mkdir(parents=True, exist_ok=True)
    (root / "transforms/metrics/px.sql").write_text(PX)
    (root / "transforms/sorter.py").write_text(step_src)
    for fname, body in (files or {}).items():
        (root / "transforms" / fname).write_text(body)
    (root / "qanat.yaml").write_text(project_yaml(name, **kw))
    migration = plan_migration(root)
    if migration.needed:
        apply_migration(migration)
        (root / "qanat.yaml.before-the-job-model").unlink(missing_ok=True)
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
        return run_all(store, project, root, as_of=as_of, ingest=False)
    finally:
        store.close_pit()
