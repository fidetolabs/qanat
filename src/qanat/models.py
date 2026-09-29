"""What a project is made of.

Eight words, and each one means exactly one thing:

    stage      an ordered stage that holds tables
    table      one dataset inside a stage, addressed `stage.table`
    source     brings a table in from outside, through a connector
    connector  how a source connects: rest, sql, csv, synthetic
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

StageKind = Literal["raw", "features", "weights", "pnl"]
Connector = Literal["rest", "sql", "csv", "synthetic"]
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
    kind: StageKind = "features"
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
    # How this step is *executed* when it is an alpha. A five-day reversal and a
    # sixty-day momentum are not asking for the same rebalance gap, and the gap is
    # part of the rule, not of the project. Left unset, the run falls back to the
    # `backtest:` block -- these only say what this alpha wants by default.
    rebalance: str | None = None
    decay: int | None = None

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


class Research(Base):
    """An unattended pass that works on the project while nobody is watching.

    The goal is fixed and narrow on purpose. `falsify` takes a strategy that
    already looks good and tries to break it -- other windows, other universes,
    other costs -- and that is the one goal that cannot overfit: every result it
    produces can only take confidence away. `monitor` compares what a live
    strategy is earning against what its backtest promised, and invents nothing
    at all.

    Searching for new strategies is deliberately not here yet. A loop that
    proposes and keeps winners needs the sealed window and the trial-count bar
    doing real work first, or it is an expensive way to fool yourself.
    """

    enabled: bool = False
    #: When to wake. Cron, like a source or a step.
    schedule: str = "0 3 * * *"
    goal: Literal["falsify", "monitor"] = "falsify"
    #: Stop a pass once it has cost this much. The CLI reports its own spend, so
    #: this is measured rather than estimated.
    budget_usd: float = 1.0
    #: How many alphas one pass will work on.
    targets: int = 1


class Bar(Base):
    """How high a result has to be, given how many were tried to find it.

    The same Sharpe means opposite things at one attempt and at fifty, and nothing
    inside a backtest can tell you which you are looking at. This is where that
    correction is written down, in the file, where it can be read back and argued
    with -- rather than living in somebody's head and being applied inconsistently.

    `gate` is off by default and that is deliberate. Turning a number into
    something that blocks a decision is a choice the person makes once, knowingly;
    until then this reports and explains and refuses nothing.
    """

    #: none  -- report the figures, require nothing.
    #: floor -- a fixed t-stat, regardless of how many were tried. 3.0 is the
    #:          bar Harvey, Liu and Zhu argued for after counting how many
    #:          factors the literature had already tested.
    #: count -- raise the bar with the number of trials, which is the one that
    #:          actually answers "was this just the luckiest of fifty".
    rule: Literal["none", "floor", "count"] = "none"
    #: The fixed bar, for `floor`.
    t_floor: float = 3.0
    #: The significance a single test would have needed, before it is divided by
    #: the number of tests. For `count`.
    alpha: float = 0.05
    #: Whether failing this stops anything, or only says so.
    gate: bool = False


class Backtest(Base):
    """How a replay turns the weights table into a net-edge number.

    The weights table says what to hold; this says what holding it costs. Every
    figure the report prints comes from these fields, so a backtest that flatters
    itself has to do it here, in the open.
    """

    prices: str                       # stage.table carrying the price per symbol per date
    price_column: str = "close"
    symbol_column: str = "symbol"
    date_column: str = "date"
    fee_bps: float = 0.0              # charged on turnover, in basis points
    slippage_bps: float = 0.0         # estimated, charged on turnover too
    rebalance: str = "1d"             # gap between as-of dates
    purge: str = "0d"                 # hold rows back this long before a step may read them
    embargo: str = "0d"               # wait this long after as_of before a return counts
    decay: int = 0                    # smooth the portfolio over this many rebalances; 0 is off
    # What the strategy has to beat to have earned anything. Without one, a
    # long-only rule in a rising market reads as skill and the report cannot say
    # otherwise -- every number it prints is the market's move plus yours, with no
    # seam between them. Three things it can be:
    #
    #   * a symbol in the prices table -- hold that one thing instead.
    #   * `equal_weight` -- hold everything the prices table carries, in equal
    #     parts. The market, as far as this project has one.
    #   * `universe_equal_weight` -- hold the run's universe on each date, in equal
    #     parts. The pool the step was choosing from, held without choosing.
    #
    # The last two are different questions, and which one is the honest floor
    # depends on what the strategy claims. A rule that only ranks inside a pool
    # somebody else chose is answerable to `universe_equal_weight`: beating the
    # whole market by riding a sector that doubled is not evidence it picked well.
    # Where the prices table and the universe are the same list, so are the two.
    benchmark: str = ""
    #: What a result has to clear before it counts, given how many were tried.
    bar: Bar | None = None
    split: str = ""                   # first out-of-sample date; before it is in-sample
    # Keep scoring forward as data arrives, instead of once over a frozen window.
    # `qanat serve` runs a pass each time the data reaches the next as-of date on
    # the rebalance grid, so `rebalance: 1d` gives a result per day.
    live: bool = False
    # Which alpha, or alphas, a live pass prices. A project holding one needs no
    # instruction and this stays empty. With several, `run_backtest` refuses to
    # choose -- rightly, since a book of two is a different strategy from either --
    # and the scheduler has no business guessing either. Naming them here is how
    # the choice gets made once, in the file, where it can be read back.
    live_alphas: list[str] = Field(default_factory=list)
    # The last date the data held when live scoring was switched on. Everything
    # after it is a period nobody could see when the alpha was chosen, which is
    # the only sense of out-of-sample that cannot be arrived at by looking.
    # Stamped once, on the first live pass -- computed fresh each time, it would
    # move forward daily and mean nothing.
    live_from: str = ""

    @field_validator("prices")
    @classmethod
    def _prices_qualified(cls, v: str) -> str:
        return _qualified([v])[0]


class Agent(Base):
    """Which installed agent CLI the unattended pass drives.

    Left unset it takes the first one it finds on PATH, which is fine
    until two are installed and the choice is the accident of ordering. Naming it
    here settles it once, in the file, where it can be read back.

    It matters more than a preference once anything runs unattended: which agent
    proposed a strategy is part of what produced the number, so a pass that ran on
    whatever PATH offered that morning is a result nobody can reproduce.
    """

    #: The binary to drive -- `claude`, `cursor-agent`. Empty means first found.
    cli: str = ""
    #: How long one question gets before the CLI is killed. A person waiting on an
    #: answer gives up long before a research pass does, so this is a setting
    #: rather than the constant it used to be.
    timeout: str = "180s"


class Project(Base):
    name: str = Field(validation_alias=AliasChoices("project", "name"))
    store: str = "./data/qanat.duckdb"
    stages: list[Stage]
    universes: list[Universe] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    retention: dict[str, str] = Field(default_factory=dict)
    time_columns: dict[str, str] = Field(default_factory=dict)
    # How long any job gets, unless it says otherwise. A job with no limit holds a
    # worker until the process ends, and four of those stop the scheduler dead.
    job_timeout: str | None = None
    backtest: Backtest | None = None
    agent: Agent | None = None
    research: Research | None = None

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

    @property
    def weights_stage(self) -> Stage | None:
        return next((x for x in self.stages if x.kind == "weights"), None)

    @property
    def pnl_stage(self) -> Stage | None:
        """Where a replay writes what an alpha earned, if the project keeps one."""
        return next((x for x in self.stages if x.kind == "pnl"), None)

    @property
    def alphas(self) -> list[tuple[str, str]]:
        """(step id, weights table) for every alpha in the project.

        **The alpha is the step from features to weights** -- the convention every
        equity shop uses, because alphas have to be comparable and combinable, and
        two alphas can only be compared if they read the same features. Its DAG is
        its *lineage*: what it depends on, shared with whatever else depends on it.
        Freeze that lineage and you have the bundle you archive to reproduce it.

        One weights table per alpha, so the book reads straight off the file.
        """
        wl = self.weights_stage
        if wl is None:
            return []
        out = []
        for st in self.steps:
            for ref in st.writes:
                if ref.startswith(f"{wl.id}."):
                    out.append((st.id, ref))
        return sorted(out)

    @staticmethod
    def alpha_name(step_id: str, ref: str = "") -> str:
        """What to call an alpha in front of a person.

        Its weights table, its PnL table and its short name are the same word, so
        one name follows it through the pipeline. The step id keeps its `alpha_`
        prefix because job ids share a namespace with the feature steps.

        Several alphas priced as one portfolio are joined by `+`, and read out the
        same way: "momentum + low_vol" is one strategy made of two.
        """
        if "+" in step_id:
            return " + ".join(x.removeprefix("alpha_") for x in step_id.split("+") if x)
        return ref.partition(".")[2] if ref else step_id.removeprefix("alpha_")

    def alpha(self, name: str) -> tuple[str, str] | None:
        """Find an alpha by its short name, its step id, or its weights table."""
        for step_id, ref in self.alphas:
            if name in (step_id, ref, ref.partition(".")[2]):
                return step_id, ref
        return None

    @property
    def jobs(self) -> list[Source | Step]:
        """Everything that runs, sources first."""
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
