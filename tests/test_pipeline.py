"""End to end: scaffold a project, run it, and look at what landed."""

from pathlib import Path

import pytest
from conftest import edit_project, write_project_text
from conftest import write_demo_project as write_project

from qanat.project import load
from qanat.runner import run_all
from qanat.store import Store


def test_run_all_lands_every_table(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))

    results = run_all(store, project, root)
    assert all(r.ok for r in results), [r for r in results if not r.ok]

    weights = store.read("features.target")
    assert not weights.empty
    assert {"symbol", "weight"} <= set(weights.columns)
    assert abs(weights["weight"].sum() - 1.0) < 1e-6

    universe = set(store.query("SELECT symbol FROM raw__daily_prices")["symbol"])
    assert set(weights["symbol"]) <= universe

    runs = store.recent_executions()
    assert {r["definition_id"] for r in runs} == {d.id for d in project.definitions}
    # Every execution names the job it was an attempt at, and the attempt number.
    assert all(r["job_id"] is not None and r["attempt"] == 1 for r in runs)
    store.close()


def test_a_step_cannot_read_what_it_did_not_declare(tmp_path: Path):
    write_project(tmp_path, "demo")
    (tmp_path / "transforms" / "momentum.py").write_text(
        "def run(ctx):\n    return ctx.read('raw.news')\n"
    )
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    results = {r.definition_id: r for r in run_all(store, project, root)}
    assert results["momentum"].status == "failed"
    assert "did not declare" in results["momentum"].error
    store.close()


def test_plan_sees_new_changed_and_orphan(tmp_path: Path):
    """The three kinds of drift a file can have against its database."""
    from qanat.plan import plan, snapshot

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))

    # 1. nothing applied yet: every table is a create
    first = plan(project, root, store)
    assert first.first_run
    assert len(first.creates) == len(project.tables())
    assert not first.orphans

    run_all(store, project, root)
    store.save_state(snapshot(project, root))
    assert plan(project, root, store).empty

    # 2. change an option and a script -> two updates, no new tables
    edit_project(tmp_path, "lookback: 20", "lookback: 60")
    (tmp_path / "transforms" / "risk.sql").write_text(
        (tmp_path / "transforms" / "risk.sql").read_text() + "\n-- edited\n"
    )
    project, root = load(tmp_path)
    p2 = plan(project, root, store)
    assert {c.target for c in p2.updates} == {"momentum", "risk"}
    assert any("lookback" in d for c in p2.updates for d in c.details)
    assert any("script contents changed" in d for c in p2.updates for d in c.details)
    assert not p2.creates and not p2.orphans

    # 3. delete the transform that owns a table -> it goes, the table is orphaned
    from qanat.editor import delete_transform

    project, root = load(tmp_path)
    # `portfolio` reads what `tone` writes, so it goes first -- and its cadence
    # goes with it, since a schedule over a definition that is not there fires
    # nothing and reads as live.
    delete_transform(project, root, "portfolio")
    delete_transform(project, root, "tone")
    project, root = load(tmp_path)
    p3 = plan(project, root, store)
    # Both transforms, and the cadences that pointed at them: `plan` watches
    # definitions and schedules alike, so a removed schedule is drift too.
    assert {c.target for c in p3.removes} == {
        "tone", "portfolio", "tone_5m", "portfolio_5m"}
    assert {c.target for c in p3.orphans} == {"features.tone", "features.target"}

    # 4. prune removes them, and then the plan is quiet about tables again
    assert store.drop("features.tone") == 8
    store.drop("features.target")
    assert not plan(project, root, store).orphans
    store.close()


# ------------------------------------- polling the same window twice
def test_a_source_key_keeps_one_row_per_key(tmp_path: Path):
    """A feed that answers with the last seven days, polled hourly, appends those
    seven days every hour. Without a key the table is mostly copies of itself."""
    import pandas as pd

    store = Store(str(tmp_path / "t.duckdb"))
    window = pd.DataFrame({"symbol": ["A", "B"], "ts": ["d1", "d1"], "px": [1.0, 2.0]})

    store.write("raw.px", window, mode="append", key=["symbol", "ts"])
    store.write("raw.px", window, mode="append", key=["symbol", "ts"])
    store.write("raw.px", pd.DataFrame({"symbol": ["A"], "ts": ["d2"], "px": [3.0]}),
                mode="append", key=["symbol", "ts"])

    got = store.read("raw.px").sort_values(["symbol", "ts"])
    assert len(got) == 3, "the repeated window did not land twice"
    assert list(got["ts"]) == ["d1", "d2", "d1"]

    # a later poll correcting a price replaces the row rather than adding one
    store.write("raw.px", pd.DataFrame({"symbol": ["A"], "ts": ["d1"], "px": [1.5]}),
                mode="append", key=["symbol", "ts"])
    fixed = store.read("raw.px")
    assert len(fixed) == 3
    assert fixed.loc[(fixed.symbol == "A") & (fixed.ts == "d1"), "px"].iloc[0] == 1.5
    store.close()


def test_without_a_key_an_append_still_appends(tmp_path: Path):
    """The key is opt-in. A source that wants every row kept says nothing."""
    import pandas as pd

    store = Store(str(tmp_path / "t.duckdb"))
    window = pd.DataFrame({"symbol": ["A", "B"], "ts": ["d1", "d1"], "px": [1.0, 2.0]})
    store.write("raw.px", window, mode="append")
    store.write("raw.px", window, mode="append")
    assert len(store.read("raw.px")) == 4
    store.close()


def test_a_key_naming_a_column_that_is_not_there_says_so(tmp_path: Path):
    import pandas as pd
    import pytest

    store = Store(str(tmp_path / "t.duckdb"))
    with pytest.raises(ValueError, match="which the rows do not have"):
        store.write("raw.px", pd.DataFrame({"sym": ["A"]}), mode="append", key=["symbol"])
    store.close()


# ------------------------------------- a step that waits on a table, not a clock
def _wire_when(root: Path) -> None:
    """Take the project off its clocks and have the transforms wait instead."""
    import yaml

    f = root / "jobs.yaml"
    spec = yaml.safe_load(f.read_text())
    spec.pop("job_schedules", None)
    for transform in spec["transform_job_definitions"]:
        transform["when"] = list(transform["from"])
    f.write_text(yaml.safe_dump(spec, sort_keys=False))


def test_new_rows_wake_the_steps_that_read_them(tmp_path: Path):
    """A clock is a guess about when the data arrives. This is the arrival."""
    write_project(tmp_path, "demo")
    _wire_when(tmp_path)
    project, _ = load(tmp_path)

    woken = project.waiting_on(["normalized.prices"])
    assert woken, "nothing woke for normalized.prices"
    assert all("normalized.prices" in s.when for s in woken)

    assert project.waiting_on(["raw.nobody_reads_this"]) == []


def test_the_chain_reaches_the_last_table(tmp_path: Path):
    """Each woken step wakes whatever waits on its own tables, so one source
    landing rows reaches the far end without anything walking the graph."""
    write_project(tmp_path, "demo")
    _wire_when(tmp_path)
    project, _ = load(tmp_path)

    frontier = list(project.ingestion_job_definitions[0].writes)
    seen, reached = set(frontier), set()
    while frontier:
        woken = [s for s in project.waiting_on(frontier) if s.id not in reached]
        reached.update(s.id for s in woken)
        frontier = [t for s in woken for t in s.writes if t not in seen]
        seen.update(frontier)

    assert "features.target" in seen, \
        f"the chain stopped short: reached {sorted(reached)}"


def test_waking_actually_runs_the_step(tmp_path: Path):
    """The pure part says who. This is the part that starts them."""
    from qanat.runner import run_ingestion
    from qanat.scheduler import Scheduler

    write_project(tmp_path, "demo")
    _wire_when(tmp_path)
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    sched = Scheduler(store, project, root)

    source = project.ingestion_job_definitions[0]
    assert run_ingestion(store, project, root, source).ok
    woken = sched.wake(source.writes)
    assert woken

    assert sched.quiet(), "the woken steps never finished"
    ran = {r["definition_id"] for r in store.recent_executions()}
    assert set(woken) <= ran, f"{set(woken) - ran} were woken but never ran"
    store.close()


def test_a_step_cannot_wait_on_a_table_it_does_not_read(tmp_path: Path):
    """The same rule as ctx.read(): if it is not declared, it does not exist."""
    import pytest

    from qanat.models import TransformJobDefinition

    with pytest.raises(ValueError, match="which it does not read"):
        TransformJobDefinition.model_validate({"id": "m", "from": ["a.b"], "to": ["c.d"],
                             "script": "s.py", "when": ["x.y"]})


# ------------------------------------- what a new project looks like on day one
def test_the_scaffold_is_a_valid_project(tmp_path: Path):
    """`qanat init` has to produce something `qanat check` accepts, without
    running it -- the scaffold points at EDGAR and a test has no network."""
    from qanat.project import validate
    from qanat.scaffold import write_project as write_scaffold

    write_scaffold(tmp_path, "demo")
    project, root = load(tmp_path)
    rep = validate(project, root)
    assert rep.ok, rep.errors


def test_the_scaffold_stops_at_metrics(tmp_path: Path):
    """No weights stage and no pnl stage. **A metric is the last thing a stage
    holds** -- it is one number per subject per date, and what somebody does
    with it is theirs.

    The split between the four is authorship rather than derivation: `raw` is as
    fetched, `normalized` is unpacking what the source gave you with no choice
    of your own, `features` is your judgement in some other shape, and `metrics`
    is your judgement as one number per subject per date.
    """
    from qanat.scaffold import write_project as write_scaffold

    write_scaffold(tmp_path, "demo")
    project, _ = load(tmp_path)
    kinds = [s.kind for s in project.stages]
    assert kinds == ["raw", "normalized", "features", "metrics"], kinds
    assert not any(s.kind in ("weights", "pnl") for s in project.stages), (
        "the five fixed kinds are gone; a stage declares what is true of it"
    )


def test_the_scaffold_dates_its_rows_by_when_they_were_published(tmp_path: Path):
    """The whole point-in-time story rests on this one line being right."""
    from qanat.scaffold import write_project as write_scaffold

    write_scaffold(tmp_path, "demo")
    project, _ = load(tmp_path)
    assert project.time_columns["raw.documents"] == "published_at"
    assert "period_end" not in project.time_columns.values(), (
        "period_end is what the numbers are about, never permission to see them"
    )


def test_the_scaffold_asks_the_sec_for_a_contact(tmp_path: Path):
    """The SEC refuses anonymous requests, so the scaffold has to prompt for one
    rather than ship a shared identity."""
    from qanat.scaffold import write_project as write_scaffold

    write_scaffold(tmp_path, "demo")
    # In `jobs.yaml`, with the rest of the selection. `qanat.yaml` is the
    # architecture and holds nothing a connector needs.
    assert "SEC_USER_AGENT" in (tmp_path / "jobs.yaml").read_text()


# ------------------------------------- the example that ships with it
def test_the_bundled_example_is_a_valid_project():
    """`examples/sec-10k` downloads forty real filings, so it cannot be run
    here. Its contract can still be checked."""
    from qanat.project import validate

    src = Path(__file__).resolve().parents[1] / "examples" / "sec-10k"
    project, root = load(src)
    rep = validate(project, root)
    assert rep.ok, rep.errors
    assert {s.id for s in project.stages} == {"raw", "normalized", "metrics"}
    assert project.time_columns["raw.documents"] == "published_at"
    # The two shipped formulas are transform jobs filling their own tables, and
    # the stage they write into is what makes them metrics.
    assert project.metrics_stage() == "metrics"
    assert set(project.metric_ids()) == {"pe", "net_margin"}
    for mid in project.metric_ids():
        assert project.definition(mid).key == ["subject", "date"], \
            "a metric value is never replaced"
        assert (root / project.definition(mid).script).is_file()


def test_every_bundled_example_passes_check():
    """`examples/` used to ship three projects that `qanat check` rejected,
    because nothing ever held them against it. This is that check."""
    from qanat.project import validate

    root = Path(__file__).resolve().parents[1] / "examples"
    found = sorted(p for p in root.iterdir() if (p / "qanat.yaml").exists())
    assert found, "no examples found at all"
    for d in found:
        project, proot = load(d)
        rep = validate(project, proot)
        assert rep.ok, f"{d.name}: {rep.errors}"


def test_init_can_be_told_where_the_store_goes(tmp_path: Path):
    """The container needs the demo built in the store it actually uses. Writing
    qanat.yaml and rewriting `store:` afterwards would build it in the wrong one."""
    import yaml
    from conftest import write_demo_project as write_project

    elsewhere = "./var/corpus"
    write_project(tmp_path, "demo", store=elsewhere)
    assert yaml.safe_load((tmp_path / "qanat.yaml").read_text())["store"] == elsewhere

    from conftest import STORE_KIND

    plain = tmp_path / "plain"
    plain.mkdir()
    write_project(plain, "demo")
    default = "./data" if STORE_KIND == "parquet" else "./data/qanat.duckdb"
    assert yaml.safe_load((plain / "qanat.yaml").read_text())["store"] == default


def test_the_docker_entrypoint_only_calls_flags_that_exist(tmp_path: Path):
    """It cannot be run here without a daemon, so at least hold its commands
    against the CLI that will run them."""
    import re
    import subprocess

    ep = (Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh").read_text()
    used = set(re.findall(r"--[a-z-]+", ep))

    known = set()
    for cmd in ("init", "mcp", "schedule"):
        out = subprocess.run(["uv", "run", "qanat", cmd, "--help"],
                             capture_output=True, text=True, timeout=120, check=True).stdout
        known |= set(re.findall(r"--[a-z-]+", out))

    unknown = used - known
    assert not unknown, f"entrypoint.sh uses flags qanat does not have: {sorted(unknown)}"

    # And the verb, not only the flags. `qanat serve` was deleted and the
    # entrypoint went on calling it, which is a container that builds and then
    # exits 2 on startup.
    from qanat.cli import MOVED

    for verb in re.findall(r"^\s*(?:exec\s+)?qanat\s+([a-z-]+)", ep, re.MULTILINE):
        assert verb not in MOVED, f"entrypoint.sh calls `qanat {verb}`, which is gone"


# ---- no job replaces provided rows ------------------------------------------
def test_a_source_declaring_mode_replace_is_refused_at_load(tmp_path: Path):
    """`qanat init` scaffolded this. With `replace` a run rewrites the table to
    only what the selection returned, so every row landed outside that list is
    deleted as the normal outcome of a job that reports ok -- which made the
    platform's main verb the thing you had to avoid to keep your data."""
    from qanat.project import ProjectError, load

    (tmp_path / "seed").mkdir()
    (tmp_path / "seed/b.csv").write_text("ts,symbol,close\n2024-01-01,A,1.0\n")
    write_project_text(tmp_path, 
        "project: t\nstore: ./data\n"
        "stages:\n  - {id: raw, kind: raw}\n"
        "ingestion_job_definitions:\n  - {id: b, to: [raw.b], connector: seed, mode: replace, "
        "options: {path: ./seed/b.csv}}\n"
    )
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "mode: replace is not allowed on an ingestion job" in str(exc.value)
    assert "mode: append" in str(exc.value), "the refusal has to name the fix"


def test_the_scaffolded_project_cannot_lose_a_row_it_did_not_fetch(tmp_path: Path):
    """The whole point of the default change: `run` is safe to call."""
    from qanat.project import load
    from qanat.scaffold import write_project as scaffold

    scaffold(tmp_path / "demo", "demo")
    project, _ = load(tmp_path / "demo")
    src = next(s for s in project.ingestion_job_definitions if s.ref == "raw.documents")
    assert src.mode == "append"
    assert src.key, "an append with no key keeps a full copy of every poll"
