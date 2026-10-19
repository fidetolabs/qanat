"""What a project is made of.

Eight words, and each one means exactly one thing:

    stage      an ordered stage that holds tables
    table      one dataset inside a stage, addressed `stage.table`
    source     brings a table in from outside, through a connector
    connector  how a source connects: rest, sql, csv, sec, yfinance
    step       a script that turns tables into tables
    universe     the symbols a portfolio is allowed to hold
    job        a source or a step -- anything that runs
    run        one execution of a job

In YAML a job says `from:` and `to:`. In Python those are `.reads` and `.writes`,
because `from` is a keyword. That is the only place a word has two spellings.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

#: What a stage declares about itself. `raw` is the only value the platform
#: acts on: a landed stage, written by sources and never by a step. Everything
#: else is a name the user chose, and `check` enforces properties rather than
#: names. This was an enum of five until the five described one workflow.
StageKind = str
Connector = Literal["rest", "sql", "csv", "sec", "yfinance"]
WriteMode = Literal["append", "replace"]

_NAME = r"^[a-z][a-z0-9_]*$"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _qualified(v: list[str]) -> list[str]:
    """Both halves of a `stage.table` reference, held to the same name rule.

    The table half used to be taken as written, and it is interpolated into DDL
    further down. A name carrying a quote is therefore a SQL statement, and one
    carrying a space or a capital is a table nobody can address again. Neither is
    something a project should be able to say by accident, and an agent writing
    this file has no reason to say it on purpose.
    """
    for ref in v:
        stage, _, table = ref.partition(".")
        if ref.count(".") != 1 or not stage or not table:
            raise ValueError(f"a table is written 'stage.table', got {ref!r}")
        for part, what in ((stage, "stage"), (table, "table")):
            if not re.match(_NAME, part):
                raise ValueError(
                    f"{what} name {part!r} in {ref!r} is not usable. Names are lower case, "
                    "start with a letter, and hold only letters, digits and underscores"
                )
    return v


class Stage(Base):
    """One stage of the pipeline. Order in the file is order in the pipeline."""

    id: str = Field(pattern=_NAME)
    kind: StageKind = ""
    description: str = ""


class Universe(Base):
    """Which symbols a portfolio may hold.

    `symbols` is a csv with a `symbol` column. If it also carries `from` and `to`
    dates, membership is **point-in-time**: a symbol counts on date D when it had
    joined by then and had not yet left. That is what stops a backtest from holding
    a name years before it listed, or from never holding one that was delisted --
    the survivorship bias that makes a static list flatter every number built on it.

    Blank `from` means "since always"; blank `to` means "still in".
    """

    id: str = Field(pattern=_NAME)
    index: str = ""
    symbols: str


class Source(Base):
    """One table, brought in from outside. Many sources may feed one stage."""

    id: str = Field(pattern=_NAME)
    writes: list[str] = Field(validation_alias=AliasChoices("to", "writes"))
    connector: Connector
    schedule: str | None = None
    mode: WriteMode = "append"
    # Which columns identify one row. A feed that answers with the last seven days
    # every time is mostly repeats, and without a key an append keeps every copy.
    key: list[str] = Field(default_factory=list)
    # How long this job gets before the scheduler stops waiting on it, e.g. "10m".
    # Unset means the project default, and that means none.
    timeout: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("writes")
    @classmethod
    def _one_table(cls, v: list[str]) -> list[str]:
        if len(v) != 1:
            raise ValueError("a source writes exactly one table")
        return _qualified(v)

    @property
    def ref(self) -> str:
        return self.writes[0]

    @property
    def stage(self) -> str:
        return self.ref.split(".")[0]

    @property
    def reads(self) -> list[str]:
        return []


class Step(Base):
    """A script that turns tables in one or more stages into tables in a later one.

    n:m -- `from` and `to` are both lists.
    """

    id: str = Field(pattern=_NAME)
    reads: list[str] = Field(default_factory=list, validation_alias=AliasChoices("from", "reads"))
    writes: list[str] = Field(default_factory=list, validation_alias=AliasChoices("to", "writes"))
    script: str
    schedule: str | None = None
    # Tables whose new rows should run this step. A clock is a guess about when
    # the data arrives; this is the arrival itself. Both may be set, and a step
    # that sets neither runs when you ask for it.
    when: list[str] = Field(default_factory=list)
    universe: str | None = None
    timeout: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reads", "writes", "when")
    @classmethod
    def _refs(cls, v: list[str]) -> list[str]:
        return _qualified(v)

    @model_validator(mode="after")
    def _waits_on_what_it_reads(self) -> Step:
        stray = [t for t in self.when if t not in self.reads]
        if stray:
            raise ValueError(
                f"step '{self.id}' waits on {', '.join(stray)}, which it does not read. "
                f"Add it to `from:`, or wait on one of {', '.join(self.reads) or '(nothing)'}"
            )
        return self


class Corpus(Base):
    """Which tables hold documents, their text and their tagged numbers.

    The reading tools need to know where to look, and the platform does not
    name layers, so the project says. Same shape as `returns:`: a default so a
    caller does not repeat it, and nothing hard-coded in the engine.

    Unset, the reading tools are simply not offered. A project of prices has
    no documents to read and should not be told it has.
    """

    documents: str = ""
    spans: str = ""
    facts: str = ""


class Metric(Base):
    """A formula with a name and a version. Shared, and not an opinion.

    `PER = price / diluted EPS` is arithmetic. There is no judgement in it, so
    it is computed once and the answer is the same for everybody. The judgement
    is the threshold somebody puts next to it, and that belongs to whoever set
    it.

    **The version is load-bearing and it is yours to bump.** A saved answer
    points at a definition version, so editing a formula in place silently
    changes what an old answer meant. Bump `version` instead: the new one is
    written alongside the old and both still resolve.
    """

    id: str = Field(pattern=_NAME)
    #: The formula, as SQL over the tables this project holds. One row out per
    #: subject, with a `value` column. `:as_of` is replaced with the date being
    #: screened, so a definition filters its own history.
    sql: str
    version: int = 1
    unit: str = ""
    description: str = ""

    @field_validator("version")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"version is {v}: versions start at 1 and only go up")
        return v


class Returns(Base):
    """Where a screen finds what a company did next.

    A screen says who passed. Whether that list was any good is answered by
    what each name returned afterwards, and that needs a price. Which table
    holds one is the project's to say, because the platform does not name
    layers: this is the same shape as passing the table as an argument, with
    a default so a caller does not repeat it on every call.

    The return is **per company, from its own date to its own next date**.
    Companies file on different days, so there is no common calendar to price
    them on, and forcing one would compare a year for one name against
    thirteen months for another.
    """

    #: `stage.table`, holding one row per subject per date with a price on it.
    table: str
    subject_column: str = "subject"
    date_column: str = "as_of"
    price_column: str = "close"


class Project(Base):
    name: str = Field(validation_alias=AliasChoices("project", "name"))
    store: str = "./data/qanat.duckdb"
    stages: list[Stage]
    universes: list[Universe] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    retention: dict[str, str] = Field(default_factory=dict)
    time_columns: dict[str, str] = Field(default_factory=dict)
    # How long any job gets, unless it says otherwise. A job with no limit holds a
    # worker until the process ends, and four of those stop the scheduler dead.
    job_timeout: str | None = None
    returns: Returns | None = None
    corpus: Corpus | None = None

    # ---- lookups -------------------------------------------------------------
    def store_url(self, root: Path) -> str:
        """Where the tables live: a DSN as written, or a path relative to the project."""
        if self.store.startswith(("postgresql://", "postgres://")):
            return self.store
        return str(root / self.store)

    def stage(self, lid: str) -> Stage | None:
        return next((x for x in self.stages if x.id == lid), None)

    def stage_index(self, lid: str) -> int:
        for i, x in enumerate(self.stages):
            if x.id == lid:
                return i
        return -1

    def universe(self, bid: str) -> Universe | None:
        return next((x for x in self.universes if x.id == bid), None)

    def metric(self, mid: str) -> Metric | None:
        return next((x for x in self.metrics if x.id == mid), None)

    @property
    def jobs(self) -> list[Source | Step]:
        return [*self.sources, *self.steps]

    def job(self, jid: str) -> Source | Step | None:
        return next((j for j in self.jobs if j.id == jid), None)

    def waiting_on(self, refs: Sequence[str]) -> list[Step]:
        """The steps that asked to run when one of these tables gets new rows."""
        touched = set(refs)
        return [s for s in self.steps if s.when and touched.intersection(s.when)]

    def producers(self) -> dict[str, str]:
        """table -> the id of the job that writes it."""
        return {ref: j.id for j in self.jobs for ref in j.writes}

    def tables(self) -> list[str]:
        return list(self.producers())
