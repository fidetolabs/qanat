"""End to end: scaffold a project, run it, and look at what landed."""

from pathlib import Path

from qanat.project import load
from qanat.runner import run_all
from qanat.scaffold import write_project
from qanat.store import Store


def test_run_all_lands_a_portfolio(tmp_path: Path):
    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    store = Store(project.store_url(root))

    results = run_all(store, project, root)
    assert all(r.ok for r in results), [r for r in results if not r.ok]

    weights = store.read("weights.target")
    assert not weights.empty
    assert {"symbol", "weight"} <= set(weights.columns)
    assert abs(weights["weight"].sum() - 1.0) < 1e-6

    universe = set(store.query("SELECT symbol FROM raw__daily_prices")["symbol"])
    assert set(weights["symbol"]) <= universe

    runs = store.recent_runs()
    assert {r["job_id"] for r in runs} == {j.id for j in [*project.sources, *project.steps]}
    store.close()


def test_a_step_cannot_read_what_it_did_not_declare(tmp_path: Path):
    write_project(tmp_path, "demo")
    (tmp_path / "steps" / "momentum.py").write_text(
        "def run(ctx):\n    return ctx.read('raw.news')\n"
    )
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    results = {r.job_id: r for r in run_all(store, project, root)}
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
    yaml = (tmp_path / "qanat.yaml").read_text().replace("lookback: 20", "lookback: 60")
    (tmp_path / "qanat.yaml").write_text(yaml)
    (tmp_path / "steps" / "risk.sql").write_text(
        (tmp_path / "steps" / "risk.sql").read_text() + "\n-- edited\n"
    )
    project, root = load(tmp_path)
    p2 = plan(project, root, store)
    assert {c.target for c in p2.updates} == {"momentum", "risk"}
    assert any("lookback" in d for c in p2.updates for d in c.details)
    assert any("script contents changed" in d for c in p2.updates for d in c.details)
    assert not p2.creates and not p2.orphans

    # 3. delete the step that owns a table -> the job goes, the table is orphaned
    yaml = (tmp_path / "qanat.yaml").read_text()
    start = yaml.index("  - id: tone")
    end = yaml.index("  - id: portfolio")
    yaml = yaml[:start] + yaml[end:]
    yaml = yaml.replace(
        "from: [features.momentum, features.risk, features.tone]",
        "from: [features.momentum, features.risk]",
    )
    (tmp_path / "qanat.yaml").write_text(yaml)
    project, root = load(tmp_path)
    p3 = plan(project, root, store)
    assert [c.target for c in p3.removes] == ["tone"]
    assert [c.target for c in p3.orphans] == ["features.tone"]

    # 4. prune removes it, and then the plan is quiet about tables again
    assert store.drop("features.tone") == 8
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
    """Take the scaffolded project off its clocks and have the steps wait instead."""
    import yaml

    f = root / "qanat.yaml"
    spec = yaml.safe_load(f.read_text())
    for job in spec["sources"]:
        job.pop("schedule", None)
    for step in spec["steps"]:
        step.pop("schedule", None)
        step["when"] = list(step["from"])
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


def test_the_chain_reaches_a_portfolio(tmp_path: Path):
    """Each woken step wakes whatever waits on its own tables, so one source
    landing rows reaches the far end without anything walking the graph."""
    write_project(tmp_path, "demo")
    _wire_when(tmp_path)
    project, _ = load(tmp_path)

    frontier = list(project.sources[0].writes)
    seen, reached = set(frontier), set()
    while frontier:
        woken = [s for s in project.waiting_on(frontier) if s.id not in reached]
        reached.update(s.id for s in woken)
        frontier = [t for s in woken for t in s.writes if t not in seen]
        seen.update(frontier)

    assert any(t.startswith("weights.") for t in seen), \
        f"the chain stopped short of a portfolio: reached {sorted(reached)}"


def test_waking_actually_runs_the_step(tmp_path: Path):
    """The pure part says who. This is the part that starts them."""
    from qanat.runner import run_source
    from qanat.scheduler import Scheduler

    write_project(tmp_path, "demo")
    _wire_when(tmp_path)
    project, root = load(tmp_path)
    store = Store(project.store_url(root))
    sched = Scheduler(store, project, root)

    source = project.sources[0]
    assert run_source(store, project, root, source).ok
    woken = sched.wake(source.writes)
    assert woken

    assert sched.quiet(), "the woken steps never finished"
    ran = {r["job_id"] for r in store.recent_runs()}
    assert set(woken) <= ran, f"{set(woken) - ran} were woken but never ran"
    store.close()


def test_a_step_cannot_wait_on_a_table_it_does_not_read(tmp_path: Path):
    """The same rule as ctx.read(): if it is not declared, it does not exist."""
    import pytest

    from qanat.models import Step

    with pytest.raises(ValueError, match="which it does not read"):
        Step.model_validate({"id": "m", "from": ["a.b"], "to": ["c.d"],
                             "script": "s.py", "when": ["x.y"]})


# ------------------------------------- what a new project looks like on day one
def test_demo_wires_the_shelf_into_an_ordinary_project(tmp_path: Path):
    """Nothing about a shelf alpha is special once it is written: each is a step
    with its own script, its own weights table and its own line in the file."""
    from qanat.scaffold import DEMO_ALPHAS, add_shelf_alphas

    write_project(tmp_path, "demo")
    added = add_shelf_alphas(tmp_path)
    assert added == [f"alpha_{n}" for n in DEMO_ALPHAS]

    project, root = load(tmp_path)
    for name in DEMO_ALPHAS:
        step = project.job(f"alpha_{name}")
        assert step is not None, f"alpha_{name} is not in the project"
        assert step.writes == [f"weights.{name}"], "each alpha writes its own table"
        assert (root / step.script).exists(), "the script was not written"
        assert step.universe, "a shelf alpha holds itself to a universe"

    # the graph the console draws is now a book, not one card
    assert len({t for _, t in project.alphas}) >= len(DEMO_ALPHAS)


def test_demo_is_idempotent(tmp_path: Path):
    from qanat.scaffold import add_shelf_alphas

    write_project(tmp_path, "demo")
    assert add_shelf_alphas(tmp_path)
    assert add_shelf_alphas(tmp_path) == [], "running it twice added the alphas again"


def test_the_demo_project_still_obeys_the_contract(tmp_path: Path):
    """Five alphas reading one table is exactly what the weights stage is for --
    but no alpha may read another, and check has to keep saying so."""
    from qanat.project import validate
    from qanat.scaffold import add_shelf_alphas

    write_project(tmp_path, "demo")
    add_shelf_alphas(tmp_path)
    project, root = load(tmp_path)
    rep = validate(project, root)
    assert rep.ok, rep.errors


# ------------------------------------- the one real dataset that ships with it
def test_the_bundled_dataset_runs_with_no_network(tmp_path: Path):
    """Synthetic data proves the pipeline runs; it cannot say whether an alpha
    works. This is real, in the repo, and needs nothing from outside."""
    import shutil

    from qanat.project import validate

    src = Path(__file__).resolve().parents[1] / "examples" / "fx-bundled"
    project_dir = tmp_path / "fx"
    shutil.copytree(src, project_dir, ignore=shutil.ignore_patterns("*.duckdb"))

    project, root = load(project_dir)
    assert validate(project, root).ok

    store = Store(project.store_url(root))
    results = run_all(store, project, root)
    assert all(r.ok for r in results), [r for r in results if not r.ok]

    rates = store.read("normalized.fx")
    assert len(rates) > 40_000, f"only {len(rates)} rates landed"
    assert str(rates["date"].min())[:4] == "1999"
    assert not store.read("weights.target").empty
    store.close()


def test_the_bundled_and_fetched_projects_differ_only_in_the_source(tmp_path: Path):
    """The README says point `sources:` at a real feed and nothing else changes.
    These two projects are that claim, written out so it can be checked."""
    import yaml

    root = Path(__file__).resolve().parents[1] / "examples"
    a = yaml.safe_load((root / "fx-bundled" / "qanat.yaml").read_text())
    b = yaml.safe_load((root / "fx-real" / "qanat.yaml").read_text())

    for part in ("stages", "steps", "universes", "backtest"):
        assert a[part] == b[part], f"{part} differs between the two fx projects"
    assert a["sources"] != b["sources"], "the sources are the whole difference"
    assert a["sources"][0]["to"] == b["sources"][0]["to"], "and they land the same table"


def test_init_can_be_told_where_the_store_goes(tmp_path: Path):
    """The container needs the demo built in the store it actually uses. Writing
    qanat.yaml and rewriting `store:` afterwards would build it in the wrong one."""
    import yaml

    from qanat.scaffold import write_project

    dsn = "postgresql://qanat:qanat@postgres:5432/qanat"
    write_project(tmp_path, "demo", store=dsn)
    assert yaml.safe_load((tmp_path / "qanat.yaml").read_text())["store"] == dsn

    plain = tmp_path / "plain"
    plain.mkdir()
    write_project(plain, "demo")
    assert yaml.safe_load((plain / "qanat.yaml").read_text())["store"] == "./data/qanat.duckdb"


def test_the_docker_entrypoint_only_calls_flags_that_exist(tmp_path: Path):
    """It cannot be run here without a daemon, so at least hold its commands
    against the CLI that will run them."""
    import re
    import subprocess

    ep = (Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh").read_text()
    used = set(re.findall(r"--[a-z-]+", ep))

    known = set()
    for cmd in ("init", "serve"):
        out = subprocess.run(["uv", "run", "qanat", cmd, "--help"],
                             capture_output=True, text=True, timeout=120, check=True).stdout
        known |= set(re.findall(r"--[a-z-]+", out))

    unknown = used - known
    assert not unknown, f"entrypoint.sh uses flags qanat does not have: {sorted(unknown)}"
