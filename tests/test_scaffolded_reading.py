"""`qanat init` has to produce a project whose reading half runs.

**0.11.0 shipped both ends of a join and nothing that makes it.** The splitter
is in `sections.py`, the readers are in `reading.py`, `models.py` declares
`Corpus.spans`, and `ingest_filings` closes by telling the user to run the
transforms "to get facts and spans" -- but the scaffold wrote `parse_facts.py`
and no producer for `spans`, and named `facts` under `corpus:` and not `spans`.
So `list_sections`, `read_section` and `search_documents` could not run on any
scaffolded project, and `read_company` returned its spans half as `[]`.

These tests hold the scaffold to the shape the readers need. They do not run
the transform -- that needs a filing -- they check that it is there, declared,
and pointed at.
"""

from __future__ import annotations

import ast
from pathlib import Path

import yaml

from qanat.project import load
from qanat.scaffold import write_project


def test_the_scaffold_writes_a_producer_for_every_table_the_corpus_names(tmp_path: Path):
    """The corpus is what the reading tools look in. A table named there with
    nothing writing it is a tool that cannot run."""
    write_project(tmp_path, "demo")
    project, _root = load(tmp_path)

    written = {t for d in project.transform_job_definitions for t in d.writes}
    written |= {t for d in project.ingestion_job_definitions for t in d.writes}

    corpus = project.corpus
    assert corpus is not None
    for which in ("documents", "spans", "facts"):
        ref = getattr(corpus, which)
        assert ref, f"`corpus.{which}` is unset, so its tools are not offered at all"
        assert ref in written, f"nothing writes `{ref}`, which `corpus.{which}` names"


def test_the_spans_table_has_a_clock(tmp_path: Path):
    """Without one it is unfilterable by date, so every as-of read over it
    answers from rows nobody could have seen yet."""
    write_project(tmp_path, "demo")
    project, _root = load(tmp_path)
    assert project.time_columns.get(project.corpus.spans) == "as_of"


def test_the_two_parsers_read_the_document_and_not_each_other(tmp_path: Path):
    """A filing is two things -- the tagged numbers and the prose -- and both
    come out of `raw.documents`, so the order between them does not matter."""
    write_project(tmp_path, "demo")
    project, _root = load(tmp_path)
    by_id = {d.id: d for d in project.transform_job_definitions}

    for name in ("parse_facts", "parse_spans"):
        assert name in by_id, f"the scaffold declares no `{name}`"
        assert list(by_id[name].reads) == ["raw.documents"]


def test_the_spans_transform_emits_every_column_the_readers_query(tmp_path: Path):
    """`reading.py` selects these by name. A producer short one column is a
    producer whose output the readers raise on."""
    write_project(tmp_path, "demo")
    script = tmp_path / "transforms" / "parse_spans.py"
    assert script.is_file()

    tree = ast.parse(script.read_text())
    emitted = {k.value for node in ast.walk(tree) if isinstance(node, ast.Dict)
               for k in node.keys if isinstance(k, ast.Constant)}
    for column in ("document", "section", "n_chars", "char_start", "char_end",
                   "rule", "text", "as_of"):
        assert column in emitted, f"`{column}` is read by reading.py and not written"


def test_a_tag_becomes_a_space_so_two_table_cells_do_not_glue(tmp_path: Path):
    """`"".join(parse(raw).itertext())` closes one cell against the next:
    `<td>12</td><td>34</td>` reads as 1,234, and a CIK beside a year came back
    as `00003201932025`. Section detection survives it -- the same 11 spans on
    a 10-Q either way -- but `read_section` returns this text."""
    import importlib.util

    write_project(tmp_path, "demo")
    spec = importlib.util.spec_from_file_location(
        "scaffolded_parse_spans", tmp_path / "transforms" / "parse_spans.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    text = module.text_of(b"<html><td>0000320193</td><td>2025</td></html>")
    assert "0000320193 2025" in text
    assert "00003201932025" not in text


def test_the_scaffolded_project_holds_the_contract(tmp_path: Path):
    """Adding a stage output and a transform must not break `qanat check`."""
    from qanat.project import validate

    write_project(tmp_path, "demo")
    project, root = load(tmp_path)
    report = validate(project, root)
    assert not report.errors, report.errors


def test_the_architecture_file_is_still_valid_yaml_with_the_new_lines(tmp_path: Path):
    write_project(tmp_path, "demo")
    body = yaml.safe_load((tmp_path / "qanat.yaml").read_text())
    assert body["corpus"]["spans"] == "normalized.spans"
    assert body["time_columns"]["normalized.spans"] == "as_of"
