"""The four objects, and the words they are named in.

A job is a definition, a schedule over it, a job for one exact datetime, and an
execution of that job. These are the tests that hold the split apart -- a
definition that learns a cadence, or a schedule that learns a selection, puts
the two concerns back in one object and the asymmetry comes back with them.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from conftest import write_project_text

from qanat.models import IngestionJobDefinition, JobSchedule, TransformJobDefinition
from qanat.project import ProjectError, load, validate
from qanat.store import Store


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transforms" / "t.sql").write_text("SELECT * FROM raw__docs")
    (tmp_path / "seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "seed" / "docs.csv").write_text("id,as_of\n1,2024-01-01\n")
    write_project_text(tmp_path, body)
    return tmp_path


BASE = """\
project: j
store: ./data
stages:
  - {id: raw, kind: raw}
  - {id: normalized, kind: normalized}
ingestion_job_definitions:
  - id: land
    to: [raw.docs]
    connector: seed
    key: [id]
    options: {path: ./seed/docs.csv}
transform_job_definitions:
  - id: shape
    from: [raw.docs]
    to: [normalized.facts]
    script: transforms/t.sql
"""


# ---- A1: neither object holds the other's fields -----------------------------
def test_a_definition_carrying_a_cadence_is_refused_at_load(tmp_path: Path):
    """Refused, not ignored. An old config that silently lost its schedule would
    keep running and quietly stop polling."""
    root = _project(tmp_path, BASE + "    schedule: \"0 6 * * *\"\n")
    with pytest.raises(ProjectError) as exc:
        load(root)
    assert "names no cadence" in str(exc.value)
    assert "job_schedules" in str(exc.value), "the refusal has to name where it goes"


def test_a_schedule_carrying_a_selection_is_refused(tmp_path: Path):
    """The other direction, and it matters as much: a schedule that could narrow
    the selection would put both concerns back in one object from its side."""
    root = _project(tmp_path, BASE + """\
job_schedules:
  - id: land_daily
    definition: land
    cron: "0 6 * * *"
    companies: [AAPL]
""")
    with pytest.raises(ProjectError) as exc:
        load(root)
    assert "names no selection" in str(exc.value)


def test_a_definition_names_no_cadence_field_at_all():
    assert "schedule" not in IngestionJobDefinition.model_fields
    assert "schedule" not in TransformJobDefinition.model_fields
    assert "cron" not in IngestionJobDefinition.model_fields


def test_a_schedule_names_no_selection_field_at_all():
    for field in ("companies", "options", "connector", "script", "writes", "reads"):
        assert field not in JobSchedule.model_fields, field


def test_a_definition_with_no_schedule_stays_ordinary_and_runnable(tmp_path: Path):
    """How the `check` project works, and it has to keep working."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    assert validate(project, where).ok
    assert project.job_schedules == []

    from qanat.runner import run_all

    store = Store(project.store_url(where))
    try:
        assert all(r.ok for r in run_all(store, project, where))
    finally:
        store.close()


# ---- A2 (half of it): one definition, one cadence ----------------------------
def test_one_definition_carries_one_cadence(tmp_path: Path):
    """**This passed until 0.12.0, and the thing it allowed is not worth having.**

    A cadence became a row so that two *selections* could run on two clocks
    without a cadence welded into either declaration, and that half stands. Two
    cadences over *one* selection is the other half, and over one selection the
    finer cron already does everything the coarser one would -- so the coarser
    one is the same fetch again on a subset of its ticks.
    """
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: land_daily,  definition: land, cron: "0 6 * * *"}
  - {id: land_weekly, definition: land, cron: "0 6 * * 1"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert not rep.ok
    assert any("land_daily" in e and "land_weekly" in e for e in rep.errors), rep.errors
    assert any("a definition takes one" in e for e in rep.errors), rep.errors


def test_one_cadence_each_over_two_definitions_still_holds(tmp_path: Path):
    """The request the plan exists for -- Microsoft daily, Apple weekly -- is two
    definitions with one schedule each, so narrowing to one clock per definition
    leaves it reachable."""
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: land_daily, definition: land, cron: "0 6 * * *"}
""")
    project, where = load(root)
    assert validate(project, where).ok
    assert [s.cron for s in project.schedules_for("land")] == ["0 6 * * *"]


# ---- two clocks over one company and form ------------------------------------
#: Two `sec` definitions landing one documents table. Who each polls is the
#: argument; everything else about them is identical.
TWO_POLLS = """\
project: j
store: ./data
stages:
  - {id: raw, kind: raw}
ingestion_job_definitions:
  - id: wide
    to: [raw.documents]
    connector: sec
    key: [id]
    options: {companies: [%s], form: [%s]}
  - id: narrow
    to: [raw.documents]
    connector: sec
    key: [id]
    options: {companies: [%s], form: [%s]}
corpus:
  documents: raw.documents
job_schedules:
%s"""

BOTH_ON_A_CLOCK = """\
  - {id: wide_daily,   definition: wide,   cron: "0 6 * * *"}
  - {id: narrow_daily, definition: narrow, cron: "0 7 * * *"}
"""


def _two_polls(tmp_path: Path, left: str, lforms: str, right: str, rforms: str,
               schedules: str = BOTH_ON_A_CLOCK) -> Path:
    write_project_text(tmp_path, TWO_POLLS % (left, lforms, right, rforms, schedules))
    return tmp_path


def test_two_clocks_polling_one_company_and_form_are_refused(tmp_path: Path):
    """**One filing fetched twice and landed once.** EDGAR rate-limits, and the
    second fetch cannot land a row the first did not -- the `key:` sees to that.
    So the cost is real and the benefit is zero."""
    project, where = load(_two_polls(tmp_path, "AAPL, MSFT", "10-K", "AAPL", "10-K"))
    rep = validate(project, where)
    assert not rep.ok
    said = " ".join(rep.errors)
    assert "wide" in said and "narrow" in said
    assert "AAPL" in said and "10-K" in said
    assert "MSFT" not in said, "the shared half is what it should name, not the rest"


def test_an_overlap_with_no_clock_on_it_is_left_alone(tmp_path: Path):
    """**A definition records what somebody decided to follow**, and by the time
    a project follows a dozen companies nearly every pair shares one. Overlap
    costs nothing until both are polling, so only then is it refused."""
    project, where = load(_two_polls(
        tmp_path, "AAPL, MSFT", "10-K", "AAPL", "10-K",
        schedules='  - {id: wide_daily, definition: wide, cron: "0 6 * * *"}\n'))
    assert validate(project, where).ok


def test_two_clocks_on_different_companies_are_fine(tmp_path: Path):
    project, where = load(_two_polls(tmp_path, "AAPL", "10-K", "MSFT", "10-K"))
    assert validate(project, where).ok


def test_two_clocks_on_different_forms_are_fine(tmp_path: Path):
    """The same filer on two forms is two piles of documents, not one twice."""
    project, where = load(_two_polls(tmp_path, "AAPL", "10-K", "AAPL", "10-Q"))
    assert validate(project, where).ok


#: Two `yfinance` definitions landing one prices table. No forms anywhere --
#: which is the case a `sec` definition naming no `form:` also lands in.
TWO_FEEDS = """\
project: j
store: ./data
stages:
  - {id: raw, kind: raw}
ingestion_job_definitions:
  - id: wide
    to: [raw.prices]
    connector: yfinance
    key: [subject, date]
    options: {companies: [%s]}
  - id: narrow
    to: [raw.prices]
    connector: yfinance
    key: [subject, date]
    options: {companies: [%s]}
job_schedules:
  - {id: wide_daily,   definition: wide,   cron: "0 6 * * *"}
  - {id: narrow_daily, definition: narrow, cron: "0 7 * * *"}
"""


def test_two_clocks_polling_one_symbol_are_refused(tmp_path: Path):
    """**The same waste as the EDGAR case, and the same rule.** Both connectors
    name their selection `companies:`; what yfinance lacks is a form, and a
    definition with no form means every form."""
    write_project_text(tmp_path, TWO_FEEDS % ("AAPL, MSFT", "AAPL"))
    project, where = load(tmp_path)
    rep = validate(project, where)
    assert not rep.ok
    said = " ".join(rep.errors)
    assert "AAPL" in said and "raw.prices" in said
    assert "MSFT" not in said
    assert " for " not in said, "there is no form to name, so none is named"


def test_two_clocks_on_different_symbols_are_fine(tmp_path: Path):
    write_project_text(tmp_path, TWO_FEEDS % ("AAPL", "MSFT"))
    project, where = load(tmp_path)
    assert validate(project, where).ok


def test_a_definition_naming_no_form_overlaps_one_that_does(tmp_path: Path):
    """No `form:` is not "no forms", it is *every* form. So a definition without
    one cannot sit on a clock beside a 10-K poll of the same filer."""
    project, where = load(_two_polls(tmp_path, "AAPL", "10-K", "AAPL", ""))
    rep = validate(project, where)
    assert not rep.ok
    assert any("AAPL" in e for e in rep.errors), rep.errors


def test_two_connectors_on_one_table_are_not_this_rule(tmp_path: Path):
    """A sec poll and a yfinance poll of one filer fetch different things. Two
    upstreams into one table is its own question, and not this one."""
    write_project_text(tmp_path, """\
project: j
store: ./data
stages:
  - {id: raw, kind: raw}
ingestion_job_definitions:
  - id: filings
    to: [raw.mixed]
    connector: sec
    key: [id]
    options: {companies: [AAPL], form: [10-K]}
  - id: prices
    to: [raw.mixed]
    connector: yfinance
    key: [id]
    options: {companies: [AAPL]}
job_schedules:
  - {id: filings_daily, definition: filings, cron: "0 6 * * *"}
  - {id: prices_daily,  definition: prices,  cron: "0 7 * * *"}
""")
    project, where = load(tmp_path)
    assert validate(project, where).ok


def test_a_transform_carries_one_cadence_too(tmp_path: Path):
    """The rule is about a definition, and a transform is one.

    Nothing outside is fetched twice here, so the cost is cheaper than the
    ingestion case -- but a transform is a function of what is upstream of it,
    so the coarser of two crons recomputes what the finer one just wrote.
    """
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: shape_hourly, definition: shape, cron: "0 * * * *"}
  - {id: shape_daily,  definition: shape, cron: "0 6 * * *"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert not rep.ok
    assert any("shape_hourly" in e and "shape_daily" in e for e in rep.errors), rep.errors


def test_a_connector_nothing_implements_is_refused_by_check(tmp_path: Path):
    """It used to reach the first poll and die inside the adapter lookup, with a
    KeyError that named no file. The registry is the roster, so this is the one
    place that can say what the names are."""
    root = _project(tmp_path, BASE.replace("connector: seed", "connector: ftp"))
    project, where = load(root)
    rep = validate(project, where)
    assert any("ftp" in e and "nothing implements" in e for e in rep.errors), rep.errors
    assert any("sec" in e and "yfinance" in e for e in rep.errors), rep.errors


def test_a_schedule_pointing_at_nothing_is_refused_by_check(tmp_path: Path):
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: ghost, definition: nobody, cron: "0 6 * * *"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert any("nobody" in e for e in rep.errors), rep.errors


def test_a_schedule_id_may_not_collide_with_a_definition_id(tmp_path: Path):
    """`plan` tracks drift on both and keys that memory on the id, so a collision
    would have one of them reporting the other's history."""
    root = _project(tmp_path, BASE + """\
job_schedules:
  - {id: land, definition: land, cron: "0 6 * * *"}
""")
    project, where = load(root)
    rep = validate(project, where)
    assert any("duplicate id" in e for e in rep.errors), rep.errors


# ---- A8: a retried job is one job and several executions ---------------------
def test_a_definition_that_fails_twice_then_succeeds_is_one_job(tmp_path: Path):
    """**The reason the job layer exists is retries, not backfills.** With one row
    per attempt and nothing grouping them, "did the 07:00 poll succeed?" had no
    object to ask."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        job = store.open_job("land", "ingestion", "2026-11-01 07:00:00", "land_daily")
        for attempt in (1, 2):
            run = store.start_execution(job, "land", "ingestion", ["raw.docs"])
            store.end_execution(run, "failed", 0, f"attempt {attempt} broke")
        # The same instant again: the same job, a third attempt.
        again = store.open_job("land", "ingestion", "2026-11-01 07:00:00", "land_daily")
        assert again == job, "a re-run opened a second job instead of a third attempt"
        run = store.start_execution(again, "land", "ingestion", ["raw.docs"])
        store.end_execution(run, "ok", 7)

        seen = store.job(job)
        assert len(seen["attempts"]) == 3
        assert [a["attempt"] for a in seen["attempts"]] == [1, 2, 3]
        assert seen["status"] == "ok", "one attempt succeeded, so the job succeeded"
        assert seen["schedule"] == "land_daily"
        assert seen["scheduled_for"].startswith("2026-11-01 07:00")
    finally:
        store.close()


def test_an_ad_hoc_pass_is_a_job_with_no_schedule(tmp_path: Path):
    """Not the absence of a job. That is what makes its rows attributable."""
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        from qanat.runner import run_ingestion

        result = run_ingestion(store, project, where,
                               project.ingestion_job_definitions[0])
        assert result.ok
        assert result.job_id is not None
        job = store.job(result.job_id)
        assert job["schedule"] is None, "an ad-hoc pass has no cadence behind it"
        assert job["definition"] == "land"
        assert len(job["attempts"]) == 1
    finally:
        store.close()


def test_an_execution_resolves_to_its_job_and_its_definition(tmp_path: Path):
    root = _project(tmp_path, BASE)
    project, where = load(root)
    store = Store(project.store_url(where))
    try:
        from qanat.runner import run_ingestion

        result = run_ingestion(store, project, where,
                               project.ingestion_job_definitions[0])
        found = store.execution(result.execution_id)
        assert found["job_id"] == result.job_id
        assert found["definition"] == "land"
        assert found["attempt"] == 1
        assert found["targets"] == ["raw.docs"]
        version = store.definition_version("land")
        assert version and version["version"], "the definition's version resolves too"
    finally:
        store.close()


# ---- A5: the vocabulary ------------------------------------------------------
#: No file is exempt. The one file that read the old spellings was the migration,
#: and there is no migration -- so a retired word anywhere in the engine is a bug.
#: A single line may still need the bare literal (the refusal that detects it);
#: that line carries `_NAMES_A_RETIRED_WORD` and says why, so the exemption is
#: one line wide and visible where it is taken, not a filename listed over here.
_VOCABULARY_EXEMPT: tuple[str, ...] = ()

_NAMES_A_RETIRED_WORD = "# names-a-retired-word"


def _documents_the_column(hit: str) -> bool:
    """Is this the connector's own note about the provenance column it writes?

    Two shapes: a row in the module's column table (`    source   'yfinance'`),
    and the value it lands (`` `source: yfinance` ``).
    """
    body = hit.split(":", 2)[-1]
    return bool(re.match(r"\s*source\s{2,}\S", body)) or "`source: " in body


def _grep(pattern: str, *paths: str) -> list[str]:
    out = subprocess.run(
        ["grep", "-rnE", pattern, *paths],
        capture_output=True, text=True, check=False, cwd=Path(__file__).parent.parent,
    )
    return [
        line for line in out.stdout.splitlines()
        if line and not any(x in line.split(":")[0] for x in _VOCABULARY_EXEMPT)
        and "__pycache__" not in line
        # A rename has to be able to name what it renamed. The retired word in
        # backticks is the sentence explaining that it is retired.
        and "`step`" not in line and "`steps:`" not in line
        and "`source`" not in line and "`sources:`" not in line
        and "`qanat plan`" not in line
        and _NAMES_A_RETIRED_WORD not in line
        # "open source" is the licence, not the config declaration
        and "open-source" not in line and "open source" not in line
    ]


def test_the_word_step_is_gone_from_the_engine():
    """`step` named a position in a sequence and said nothing about a table going
    in and a table coming out, which is the only thing one does."""
    hits = _grep(r"\b(step|steps)\b", "src/qanat", "docs")
    # These tool names still carry the word. Renaming them is a separate
    # decision, to be made from a flow rather than from this test, so they are
    # named here rather than silently passed over.
    hits = [h for h in hits if not any(
        name in h for name in ("list_definitions", "define_transform", "read_script", "remove_transform"))]
    assert not hits, "\n".join(hits)


def test_the_word_source_survives_only_as_row_provenance():
    """It meant four things: a config declaration, a module in here, the
    `connector:` value, and a string on every row. Only the last was ever
    unambiguous, and it is the only one that keeps the word."""
    hits = [
        h for h in _grep(r"\bsources?\b", "src/qanat", "docs")
        # the provenance column, named as a key or described in prose about it
        if '"source"' not in h and "'source'" not in h
        and "source_keys" not in h and "source_id" not in h
        and "sec-edgar" not in h
        # A connector documents the column it writes, which A5 allows by name:
        # a row in its column table, or the value it puts there.
        and not _documents_the_column(h)
        # the paragraph in each module that explains why the word went
        and "qanat/sources" not in h and "was `qanat/sources/`" not in h
        # as above: renaming `save_source` is a separate decision, from a flow
        and "define_ingestion" not in h
    ]
    assert not hits, "\n".join(hits)


def test_the_connector_package_is_named_for_what_it_is():
    """**Two adapters ship**, which is the pipeline this product is. `rest`,
    `sql` and `csv` were here until 0.12.0 and no shipped project declared one.
    `seed` is the suite's own, registered by the autouse fixture, so it is
    subtracted by name rather than silently tolerated."""
    import qanat.connectors

    assert set(qanat.connectors.ADAPTERS) - {"seed"} == {"sec", "yfinance"}
    for gone in ("qanat.sources", "qanat.connectors.rest",
                 "qanat.connectors.sql", "qanat.connectors.csv_connector"):
        with pytest.raises(ImportError):
            __import__(gone)


def test_the_old_spellings_are_refused_rather_than_aliased(tmp_path: Path):
    """An alias leaves both vocabularies in the record, which is the state this
    change exists to end."""
    # Written whole on purpose: this is the file a migration reads.
    (tmp_path / "transforms").mkdir(parents=True, exist_ok=True)
    (tmp_path / "transforms" / "t.sql").write_text("SELECT 1")
    (tmp_path / "qanat.yaml").write_text(
        BASE.replace("ingestion_job_definitions:", "sources:"))
    with pytest.raises(ProjectError) as exc:
        load(tmp_path)
    assert "old vocabulary" in str(exc.value)
    assert "no converter" in str(exc.value), "the refusal says there is nothing to run"
