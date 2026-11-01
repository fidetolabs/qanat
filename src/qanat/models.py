"""What a project is made of.

Nine words, and each one means exactly one thing:

    stage       an ordered stage that holds tables
    table       one dataset inside a stage, addressed `stage.table`
    connector   how an ingestion job reaches outside: rest, sql, csv, sec, yfinance

    ingestion_job_definition   what to fetch, and which table it lands in
    transform_job_definition   which tables go in, which come out, and the script
    job_schedule               one cron line, over one definition
    job                        one definition, for one exact datetime
    job_execution              one attempt at one job

**A definition names no cadence and a schedule names no selection.** They were
one object called `source`, with `schedule:` welded on as a field, so the three
edits a person thinks of as separate -- *what to fetch*, *how often*, *which
table* -- were one edit, and "Microsoft daily, Apple weekly" had no answer.

**`source` and `step` are gone.** `source` meant four things: a config
declaration, an adapter module, the `connector:` value, and a provenance string
on a row. Only the last was ever unambiguous and it is the only one that keeps
the word. `step` named a position in a sequence and said nothing about a table
going in and a table coming out, which is the only thing one does.

In YAML a definition says `from:` and `to:`. In Python those are `.reads` and
`.writes`, because `from` is a keyword. That is the only place a word has two
spellings.
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
#: acts on: a landed stage, written by ingestion and never by a transform. Everything
#: else is a name the user chose, and `check` enforces properties rather than
#: names. This was an enum of five until the five described one workflow.
StageKind = str
Connector = Literal["rest", "sql", "csv", "sec", "yfinance"]
#: Where a metric definition came from. Shipped with the product, or written
#: here by whoever runs this Qanat.
MetricOrigin = Literal["shipped", "defined"]
#: How a job lands its rows. `replace` is still how a derived table is rebuilt --
#: it is a function of what is upstream of it, so rewriting it loses nothing. A
#: An ingestion job is the other case: its rows are the thing nothing else can
#: rebuild, and
#: `replace` there deletes every row outside the current selection as the normal
#: outcome of a successful poll. `Source` refuses it; see `Source._no_replace`.
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


#: What a definition is refused for carrying, and the object that holds it
#: instead. Refused rather than moved: a file that says `schedule:` was written
#: expecting one cadence welded to one selection, and quietly reading it as a
#: schedule row would be a different pipeline than the one on the page.
_CADENCE = ("schedule", "cron", "every")


def _no_cadence(raw: Any, what: str) -> Any:
    if not isinstance(raw, dict):
        return raw
    found = [k for k in _CADENCE if k in raw]
    if found:
        raise ValueError(
            f"a {what} names no cadence, and this one says `{found[0]}:`. How often a "
            f"definition runs is a `job_schedules:` entry pointing at it -- "
            f"{{id: <name>, definition: {raw.get('id', '<this one>')!s}, cron: ...}} -- so "
            f"one definition can carry a daily and a weekly without being copied, and so "
            f"changing the cadence is not an edit to the selection"
        )
    return raw


class IngestionJobDefinition(Base):
    """What to fetch, and the one table it lands in. **No cadence.**

    Many ingestion jobs may feed one stage, and after
    `many-schedules-many-definitions` several may feed one table -- every row
    carries the execution that wrote it, so the store can still say which pass
    landed which row.
    """

    id: str = Field(pattern=_NAME)
    writes: list[str] = Field(validation_alias=AliasChoices("to", "writes"))
    connector: Connector
    #: Append, and only append. The field stays so a config that says `replace`
    #: is refused by name rather than quietly meaning something else now.
    mode: WriteMode = "append"
    # Which columns identify one row. A feed that answers with the last seven days
    # every time is mostly repeats, and without a key an append keeps every copy.
    key: list[str] = Field(default_factory=list)
    # How long this job gets before the scheduler stops waiting on it, e.g. "10m".
    # Unset means the project default, and that means none.
    timeout: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _cadence_elsewhere(cls, raw: Any) -> Any:
        return _no_cadence(raw, "definition")

    @field_validator("writes")
    @classmethod
    def _one_table(cls, v: list[str]) -> list[str]:
        if len(v) != 1:
            raise ValueError("an ingestion job definition writes exactly one table")
        return _qualified(v)

    @field_validator("mode")
    @classmethod
    def _no_replace(cls, v: str) -> str:
        """An ingestion job may not replace what it already landed.

        `replace` rewrites the table to **only what this poll returned**, so every
        row landed outside the current selection is deleted -- silently, as the
        normal outcome of a run that reports ok. `qanat init` scaffolded exactly
        that, which put a fresh project one `run` away from losing filings nobody
        asked it to lose.

        Refused rather than corrected, because a config that says `replace` was
        written expecting the table to be rewritten, and quietly appending instead
        is a different pipeline than the one on the page.
        """
        if v == "replace":
            raise ValueError(
                "mode: replace is not allowed on an ingestion job. It rewrites the table "
                "to only what this poll returned, so every row landed outside the current "
                "selection is deleted by a run that reports ok. Use `mode: append` with "
                "`key:` naming the columns that identify one row -- a row that arrives "
                "again with the same values is not stored twice, and one that arrives with "
                "a changed value is written beside the old one instead of over it"
            )
        return v

    @property
    def ref(self) -> str:
        return self.writes[0]

    @property
    def stage(self) -> str:
        return self.ref.split(".")[0]

    @property
    def reads(self) -> list[str]:
        return []


class TransformJobDefinition(Base):
    """Tables in, tables out, through a script. **No cadence.**

    n:m -- `from` and `to` are both lists. The word is `transform` rather than
    `step` because `step` named a position in a sequence, and a position says
    nothing about a table going in and a table coming out.
    """

    id: str = Field(pattern=_NAME)
    reads: list[str] = Field(default_factory=list, validation_alias=AliasChoices("from", "reads"))
    writes: list[str] = Field(default_factory=list, validation_alias=AliasChoices("to", "writes"))
    script: str
    #: Which columns identify one row of the output. **With a key, a transform
    #: appends and never replaces**: a row identical to one already stored is not
    #: stored twice, and one that differs is written beside the old row.
    #:
    #: Without one it rewrites its table, which is right for an ordinary
    #: transform -- the output is a function of what is upstream of it, so
    #: recomputing loses nothing. It is wrong for a materialised metric: a
    #: corrected price has to leave the value somebody was already shown
    #: readable, and the only way to do that is to add a row.
    key: list[str] = Field(default_factory=list)
    # Tables whose new rows should run this transform. A clock is a guess about
    # when the data arrives; this is the arrival itself. Both may be set, and a
    # transform on neither runs when you ask for it.
    when: list[str] = Field(default_factory=list)
    timeout: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _cadence_elsewhere(cls, raw: Any) -> Any:
        return _no_cadence(raw, "definition")

    @field_validator("reads", "writes", "when")
    @classmethod
    def _refs(cls, v: list[str]) -> list[str]:
        return _qualified(v)

    @model_validator(mode="after")
    def _waits_on_what_it_reads(self) -> TransformJobDefinition:
        stray = [t for t in self.when if t not in self.reads]
        if stray:
            raise ValueError(
                f"transform '{self.id}' waits on {', '.join(stray)}, which it does not read. "
                f"Add it to `from:`, or wait on one of {', '.join(self.reads) or '(nothing)'}"
            )
        return self


#: What a schedule is refused for carrying. A cadence over a *different*
#: selection is a second definition, not a field on the clock -- and a schedule
#: that could narrow the selection would put the two concerns back in one object
#: from the other side.
_SELECTION = ("companies", "forms", "form", "from", "to", "reads", "writes",
              "connector", "script", "options", "years", "key", "mode", "when")


class JobSchedule(Base):
    """One cron line over one definition. **No selection.**

    A cadence used to be a string field on the definition, so one definition
    could have exactly one, and "Microsoft daily, Apple weekly" meant editing the
    selection of four companies into two definitions by hand. A row instead: a
    definition may carry none, one, or several, and disabling one disturbs
    neither the others nor the definition.

    Zero schedules is the ordinary case, not a gap. A definition on no clock runs
    when somebody asks for it, which is how the `check` project works.
    """

    id: str = Field(pattern=_NAME)
    #: The definition this fires. Either kind -- the kind is the definition's,
    #: and a schedule does not need to know it.
    definition: str = Field(pattern=_NAME)
    cron: str
    #: Off without being deleted, because a subscription somebody paused is not a
    #: subscription somebody never had.
    enabled: bool = True

    @model_validator(mode="before")
    @classmethod
    def _selection_elsewhere(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        found = [k for k in _SELECTION if k in raw]
        if found:
            raise ValueError(
                f"a schedule names no selection, and this one says `{found[0]}:`. What to "
                f"fetch is the definition's, and a schedule points at one by id. Two "
                f"cadences over one selection are two schedules over one definition; two "
                f"selections are two definitions"
            )
        return raw

    @field_validator("cron")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError(f"'{v}' is not a valid cron expression")
        return v


#: Either kind. The four objects are the same for both, and most of the engine
#: does not care which it has -- it asks the definition what it reads and writes.
Definition = IngestionJobDefinition | TransformJobDefinition


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
    #: Where computed metric values land. **One row per subject per date per
    #: metric per version**, which is what makes a metric a time series rather
    #: than an answer to one call: `PER on 2024-05-03` is
    #: `price(2024-05-03) / EPS(latest filing as of 2024-05-03)`, and the date is
    #: the metric's own. Unset, a metric is computed when it is asked for and
    #: nothing is stored.
    metrics: str = ""


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
    #: Where this definition came from. `shipped` is the starter set that comes
    #: with the product; `defined` is one somebody wrote here. A caller's own
    #: definition does not join the shipped set by being stored next to it, and
    #: a reader asking which it has is asking a real question.
    origin: MetricOrigin = "defined"

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
    ingestion_job_definitions: list[IngestionJobDefinition] = Field(default_factory=list)
    transform_job_definitions: list[TransformJobDefinition] = Field(default_factory=list)
    #: Cadence, as rows. Several may point at one definition; a definition with
    #: none is ordinary and runs when asked.
    job_schedules: list[JobSchedule] = Field(default_factory=list)
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
        """Where the tables live: a path, relative to the project."""
        return str(root / self.store)

    def stage(self, lid: str) -> Stage | None:
        return next((x for x in self.stages if x.id == lid), None)

    def stage_index(self, lid: str) -> int:
        for i, x in enumerate(self.stages):
            if x.id == lid:
                return i
        return -1

    def metric(self, mid: str, version: int | None = None) -> Metric | None:
        """One definition. The newest version unless a version is named.

        **Every version stays resolvable, and that is the point of the field.**
        An answer somebody kept points at a definition version, so a formula
        bumped to N+1 must not change what version N meant. Asking without a
        version gets the current one, which is what a fresh screen wants.
        """
        these = [x for x in self.metrics if x.id == mid]
        if not these:
            return None
        if version is not None:
            return next((x for x in these if x.version == version), None)
        return max(these, key=lambda x: x.version)

    def metric_versions(self, mid: str) -> list[int]:
        """Every version of one definition, oldest first."""
        return sorted(x.version for x in self.metrics if x.id == mid)

    def metric_ids(self) -> list[str]:
        """Each definition once, in the order the file declares them."""
        return list(dict.fromkeys(x.id for x in self.metrics))

    # ---- definitions ---------------------------------------------------------
    @property
    def definitions(self) -> list[Definition]:
        """Everything that runs, both kinds, in file order."""
        return [*self.ingestion_job_definitions, *self.transform_job_definitions]

    def definition(self, did: str) -> Definition | None:
        return next((d for d in self.definitions if d.id == did), None)

    def waiting_on(self, refs: Sequence[str]) -> list[TransformJobDefinition]:
        """The transforms that asked to run when one of these tables gets rows."""
        touched = set(refs)
        return [d for d in self.transform_job_definitions
                if d.when and touched.intersection(d.when)]

    # ---- schedules -----------------------------------------------------------
    def schedules_for(self, did: str) -> list[JobSchedule]:
        """Every cadence over one definition. Several is ordinary; none is too."""
        return [s for s in self.job_schedules if s.definition == did]

    def schedule(self, sid: str) -> JobSchedule | None:
        return next((s for s in self.job_schedules if s.id == sid), None)

    # ---- tables --------------------------------------------------------------
    def writers(self) -> dict[str, list[str]]:
        """table -> every definition id that writes it.

        A list, not one id. `producers()` was a dict keyed on the table, so a
        second definition writing the same table disappeared from it silently --
        which is why `check` had to refuse that outright, and why the refusal
        could only be lifted once every row named the execution that wrote it.
        """
        out: dict[str, list[str]] = {}
        for d in self.definitions:
            for ref in d.writes:
                out.setdefault(ref, []).append(d.id)
        return out

    def tables(self) -> list[str]:
        return list(self.writers())
