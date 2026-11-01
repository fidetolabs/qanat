"""Load a qanat.yaml, and enforce the stage contract.

The contract is the opinion of this tool. It is what stops a pipeline from
becoming a pile of tables nobody can explain:

  1. `raw` is landed and never edited -- no transform may write into a raw stage.
  2. Data only ever moves forward. A transform may read from its own stage (a
     feature chain) but never from a later one.
  3. Every table read is written by something.
  4. Every row carries both dates, and a screen filters on the public one.

**It is four properties and no names**, which `docs/contract.md` has said for
some time and this module did not. Two more rules used to sit here, ordering a
`weights` stage and a `pnl` stage after it. Those two stage kinds left the engine
in 0.5.0 and their rules did not: a project that stops at clean dated facts is a
complete project, because a metric is a query over those facts and a screen is a
call that returns a list, and neither needs a stage to land in. **A kind is now
a name you choose, so a rule keyed on one of five fixed words could only be
enforced against projects that happened to use them.**
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from qanat.models import Project

#: `${name}` in a .sql transform body -- the pattern the runner substitutes with.
_VAR = re.compile(r"\$\{([a-zA-Z0-9_]+)\}")


class ProjectError(Exception):
    pass


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def find_project(start: str | os.PathLike[str] | None = None) -> Path:
    """Walk up from `start` looking for qanat.yaml."""
    here = Path(start or Path.cwd()).resolve()
    for cand in [here, *here.parents]:
        f = cand / "qanat.yaml"
        if f.is_file():
            return f
    raise ProjectError("no qanat.yaml found here or in any parent directory")


def load(path: str | os.PathLike[str] | None = None) -> tuple[Project, Path]:
    """Return the parsed project and its root directory.

    A field the model refuses comes back as a `ProjectError` naming the key and
    saying what to write instead. Pydantic's own text is accurate and reads as a
    stack of model internals -- and the refusals in `models.py` exist to be read
    by whoever has to edit the file, so the message is what reaches them.
    """
    from pydantic import ValidationError

    f = Path(path).resolve() if path else find_project()
    if f.is_dir():
        f = f / "qanat.yaml"
    with f.open() as fh:
        raw = yaml.safe_load(fh) or {}
    from qanat.migrate import OLD_KEYS, looks_old

    if looks_old(raw):
        said = ", ".join(f"`{k}:`" for k in OLD_KEYS if k in raw)
        raise ProjectError(
            f"{f.name} is written in the old vocabulary: it says {said}.\n"
            "  `sources:` is now `ingestion_job_definitions:` and `steps:` is "
            "`transform_job_definitions:`,\n"
            "  and the `schedule:` field on either one is a `job_schedules:` row pointing "
            "at it by id --\n"
            "  which is what lets one definition carry a daily and a weekly.\n"
            f"  Run `qanat migrate {f.parent}` to rewrite it. It keeps a copy of this file "
            "beside the new one."
        )
    try:
        return Project.model_validate(raw), f.parent
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            where = ".".join(str(x) for x in err["loc"])
            msg = err["msg"].removeprefix("Value error, ")
            lines.append(f"{where}: {msg}" if where else msg)
        raise ProjectError(f"{f.name} cannot be loaded.\n  " + "\n  ".join(lines)) from exc


def validate(p: Project, root: Path) -> Report:
    r = Report()
    stage_ids = [x.id for x in p.stages]

    if len(set(stage_ids)) != len(stage_ids):
        r.errors.append("stage ids must be unique")
    if not p.stages:
        r.errors.append("a project needs at least one stage")

    # ---- rule 2a: normalized sits between raw and features -------------------
    # `normalized` became a kind of its own on 2026-10-11. Before that the stage
    # holding point-in-time facts carried `kind: features`, so the contract could
    # not say where it belonged. It can now, and the rule is the obvious one: after
    # raw, before anything computed.
    norms = [x for x in p.stages if x.kind == "normalized"]
    if len(norms) > 1:
        r.errors.append(
            f"at most one stage may have kind 'normalized', found {len(norms)}")
    for nz in norms:
        i = p.stage_index(nz.id)
        for other in p.stages:
            j = p.stage_index(other.id)
            if other.kind == "raw" and j > i:
                r.errors.append(
                    f"the raw stage '{other.id}' comes after '{nz.id}'. Rows land in raw "
                    "and are typed in normalized, so raw is first")
            if other.kind == "features" and j < i:
                r.errors.append(
                    f"the features stage '{other.id}' comes before '{nz.id}'. Nothing is "
                    "computed from rows that have not been typed yet")

    writers = p.writers()

    # One table, one writer. **Still here, and it is the thing row-level
    # provenance replaces rather than something to relax first.** Its stated
    # reason is that otherwise nothing can say which execution made the rows in
    # a table -- so dropping it before every row carries that would leave the
    # store unable to answer the question the rule was protecting.
    for ref, who in sorted(writers.items()):
        if len(who) > 1:
            r.errors.append(
                f"'{ref}' is written by {' and '.join(sorted(who))}. One table has one "
                "writer, or nothing can say which execution made the rows in it"
            )

    # ---- one id space ---------------------------------------------------------
    # Definitions and schedules share it, because `plan` tracks drift on both and
    # keys that memory on the id. A schedule called `filings` beside a definition
    # called `filings` would have one of them reporting the other's history.
    seen_ids: set[str] = set()
    for obj in [*p.definitions, *p.job_schedules]:
        if obj.id in seen_ids:
            r.errors.append(
                f"duplicate id '{obj.id}'. Definitions and schedules share one id space")
        seen_ids.add(obj.id)

    # ---- ingestion job definitions --------------------------------------------
    for s in p.ingestion_job_definitions:
        if s.stage not in stage_ids:
            r.errors.append(
                f"ingestion job '{s.id}' writes to unknown stage '{s.stage}'")
        if not s.key:
            r.warnings.append(
                f"ingestion job '{s.id}' has no `key:`, so every poll keeps a full copy of "
                f"whatever the feed answers. Name the columns that identify one row -- a row "
                f"that arrives again unchanged is then not stored twice, and one that arrives "
                f"with a changed value is written beside the old one"
            )

    # ---- schedules -------------------------------------------------------------
    for sch in p.job_schedules:
        if p.definition(sch.definition) is None:
            r.errors.append(
                f"schedule '{sch.id}' fires '{sch.definition}', which is not a definition in "
                f"this project. Known: "
                f"{', '.join(d.id for d in p.definitions) or '(none)'}"
            )

    # ---- transform job definitions --------------------------------------------
    for st in p.transform_job_definitions:
        if not st.writes:
            r.errors.append(f"transform '{st.id}' writes nothing")
        script = root / st.script
        try:
            inside = script.resolve().is_relative_to(root.resolve())
        except (OSError, ValueError):  # a path that cannot be resolved is not inside
            inside = False
        if not inside:
            r.errors.append(
                f"transform '{st.id}': script '{st.script}' is outside the project. A transform runs "
                "the file it names, so it has to be a path under the project directory"
            )
        elif not script.is_file():
            r.errors.append(f"transform '{st.id}': script not found at {st.script}")
        elif script.suffix not in (".sql", ".py"):
            r.errors.append(f"transform '{st.id}': script must be .sql or .py, got {script.suffix}")
        elif script.suffix == ".sql" and len(st.writes) != 1:
            r.errors.append(f"transform '{st.id}': a .sql transform writes exactly one table")

        if st.universe and p.universe(st.universe) is None:
            r.errors.append(f"transform '{st.id}' uses unknown universe '{st.universe}'")
        if not st.universe and script.is_file() and script.suffix == ".py":
            try:
                if "ctx.universe(" in script.read_text():
                    r.errors.append(
                        f"transform '{st.id}' calls ctx.universe() but has no `universe:` set. "
                        f"A transform that holds a basket does, so this fails on the first run "
                        f"with 'has no universe set'"
                    )
            except OSError:
                pass

        for ref in st.reads:
            lid = ref.split(".")[0]
            if lid not in stage_ids:
                r.errors.append(f"transform '{st.id}' reads unknown stage '{lid}'")
            if ref not in writers:
                r.errors.append(f"transform '{st.id}' reads '{ref}', which nothing produces")

        for ref in st.writes:
            lid = ref.partition(".")[0]
            lay = p.stage(lid)
            if lay is None:
                r.errors.append(f"transform '{st.id}' writes unknown stage '{lid}'")
                continue
            # rule 1
            if lay.kind == "raw":
                r.errors.append(
                    f"transform '{st.id}' writes into raw stage '{lid}'. "
                    "raw is landed as it arrived and never edited"
                )
            # rule 2
            ti = p.stage_index(lid)
            for src in st.reads:
                si = p.stage_index(src.split(".")[0])
                if si > ti:
                    r.errors.append(
                        f"transform '{st.id}' reads '{src}' from a later stage than it writes "
                        f"('{ref}'). Data only moves forward"
                    )
                elif si == ti and lay.kind != "features":
                    r.errors.append(
                        f"transform '{st.id}' reads and writes the same stage '{lid}'. "
                        "only a features stage may chain"
                    )
            if ref in st.reads:
                r.errors.append(f"transform '{st.id}' reads and writes the same table '{ref}'")

    for b in p.universes:
        if not (root / b.symbols).is_file():
            r.errors.append(f"universe '{b.id}': symbols file not found at {b.symbols}")

    from qanat.retention import parse_duration

    for definition in p.definitions:
        if definition.timeout:
            try:
                parse_duration(definition.timeout, "timeout")
            except ValueError as exc:
                r.errors.append(f"definition '{definition.id}': {exc}")
    if p.job_timeout:
        try:
            parse_duration(p.job_timeout, "job_timeout")
        except ValueError as exc:
            r.errors.append(str(exc))

    # A file store outside the project is not wrong, but it is worth saying out loud:
    # the project and its data then move separately, and a copy of the directory is
    # no longer a copy of the work.
    try:
        inside = (root / p.store).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        inside = False
    if not inside:
        r.warnings.append(
                f"store '{p.store}' is outside the project directory, so copying or moving "
                f"the project will not take its data along"
            )

    for ref, policy in p.retention.items():
        if ref.count(".") != 1:
            r.errors.append(f"retention key must be stage.table, got {ref!r}")
        else:
            lid = ref.split(".")[0]
            if lid not in stage_ids:
                r.errors.append(f"retention '{ref}' references unknown stage '{lid}'")
            try:
                from qanat.retention import MIN_RETENTION

                d = parse_duration(policy)
                if d < MIN_RETENTION:
                    r.errors.append(
                        f"retention '{ref}': {policy!r} is shorter than the one-hour floor. "
                        f"'1s' and '1d' are one keystroke apart and this deletes rows"
                    )
                elif (lay := p.stage(lid)) and lay.kind == "raw":
                    r.warnings.append(
                        f"retention '{ref}' deletes rows from a raw stage. raw is the record of "
                        f"what arrived, and it is the one thing a screen cannot rebuild -- every "
                        f"other table is a function of it"
                    )
            except ValueError as exc:
                r.errors.append(f"retention '{ref}': {exc}")

    # ---- the graph has to be a graph ------------------------------------------
    # `runner.order()` gives up on a cycle and appends what is left in file order,
    # with a comment saying `qanat check` reports it. It did not. On an empty store
    # the run fails with a confusing LookupError; on a populated one every transform
    # reports ok and the values grow on every pass, so the answer depends on how
    # many times the pipeline was run.
    remaining = list(p.transform_job_definitions)
    produced = {d.ref for d in p.ingestion_job_definitions}
    while True:
        ready = [s for s in remaining if all(rd in produced for rd in s.reads)]
        if not ready:
            break
        for s in ready:
            produced.update(s.writes)
            remaining.remove(s)
    stuck = [s for s in remaining if all(rd in writers for rd in s.reads)]
    if stuck:
        r.errors.append(
            "these transforms depend on each other in a loop, so nothing can run first: "
            + ", ".join(sorted(s.id for s in stuck))
        )

    # ---- a ${name} in a .sql transform needs an option behind it --------------------
    # Left unresolved the text stays in the query as a valid string literal that
    # matches nothing, so a typo in a YAML key empties the table and reports ok.
    for st in p.transform_job_definitions:
        f = root / st.script
        if f.suffix != ".sql" or not f.is_file():
            continue
        try:
            body = f.read_text()
        except OSError:
            continue
        from qanat.store import tables_named

        stray = sorted(
            ref for ref in tables_named(body)
            if ref in writers and ref not in st.reads and ref not in st.writes
        )
        if stray:
            r.errors.append(
                f"transform '{st.id}': {Path(st.script).name} reads {', '.join(stray)} without "
                f"declaring {'it' if len(stray) == 1 else 'them'} in `from:`. The graph is "
                f"drawn from what a transform declares, so an undeclared read is an arrow nobody "
                f"can see -- and `qanat plan` will not mark this transform stale when that table "
                f"changes"
            )

        for name in sorted(set(_VAR.findall(body))):
            if name == "as_of":
                continue
            if name not in st.options:
                r.errors.append(
                    f"transform '{st.id}': {Path(st.script).name} uses ${{{name}}}, which is not in "
                    f"its options ({', '.join(sorted(st.options)) or 'none set'})"
                )
        if "as_of" in st.options:
            r.warnings.append(
                f"transform '{st.id}' sets an option called 'as_of'. A screen overwrites it with "
                "the date being screened, so this transform means something different under "
                "a screen than under an ordinary run"
            )

    # ---- metrics --------------------------------------------------------------
    # A bumped version is written beside the old one, so an id appears more than
    # once on purpose. The pair has to be unique, or `metric(id, version)`
    # resolves to whichever one happens to be first.
    seen: set[tuple[str, int]] = set()
    for m in p.metrics:
        if (m.id, m.version) in seen:
            r.errors.append(
                f"metric '{m.id}' is defined twice at version {m.version}. A changed "
                f"formula is a new version written beside the old one, not a second "
                f"copy of the same one")
        seen.add((m.id, m.version))

    # ---- soft advice ----------------------------------------------------------
    #: A metric reads a table the way SQL does, by its physical name. Counting
    #: only transforms as readers meant the terminal table of every correct filings
    #: pipeline was reported as read by nothing -- the thing a metric is for.
    from qanat.metrics import tables_read

    read_by_metric: set[str] = set()
    for m in p.metrics:
        read_by_metric.update(tables_read(p, m.sql))

    #: And a table named in `corpus:` is read by the reading tools, which are a
    #: reader like any other -- `list_sections` and `read_facts` do nothing else.
    #: A project of filings was being told its documents, spans and facts were
    #: read by nothing, which is every table it has.
    if p.corpus:
        read_by_metric.update(
            ref for ref in (p.corpus.documents, p.corpus.spans, p.corpus.facts) if ref)

    for ref, who in writers.items():
        read_by = [st.id for st in p.transform_job_definitions if ref in st.reads]
        if not read_by and ref not in read_by_metric:
            r.warnings.append(f"'{ref}' (from {', '.join(who)}) is never read by anything")

    return r


def edges(p: Project) -> list[tuple[str, str, str]]:
    """(from_table, to_table, definition_id) for every path a row can take."""
    out = []
    for st in p.transform_job_definitions:
        for a in st.reads:
            for b in st.writes:
                out.append((a, b, st.id))
    return out
