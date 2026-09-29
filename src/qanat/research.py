"""The outer loop: a pass that works on the project with nobody watching.

Everything else in qanat is driven from outside. A person asks, or a clock fires a
job whose work was decided when it was written. Neither of those decides *what to
do next*, and that decision is the whole difference between a tool and an agent --
so until now it lived in somebody's head, and the project could only improve while
they were sitting in front of it.

**The goal is fixed and the search is not.** A loop that proposes strategies and
keeps the winners is an overfitting machine wearing a cron expression: it converges,
it produces a beautiful equity curve, and the curve measures how many times it
looked. So the first goal here is the opposite one. `falsify` takes a strategy that
already looks good and tries to break it -- other windows, other universes, other
costs. Every result it can produce takes confidence away. It cannot invent an edge,
which makes it the right thing to build first and the right thing to trust.

`monitor` is the other safe goal: compare what a live strategy is earning against
what its backtest promised. It invents nothing either.

Searching for new strategies is deliberately absent. It needs the sealed window and
the trial-count bar doing real work -- not reporting, gating -- before it is
anything other than an expensive way to fool yourself.

**It runs in its own lane.** A pass and a person must not be able to clobber each
other: one `_ask` slot meant the newest question killed whatever was running, which
is right for two questions from the same person and wrong when one of them is a
machine that woke up at three in the morning.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Any

from qanat.models import Project
from qanat.store import Store


def target_alphas(store: Store, project: Project, want: int) -> list[str]:
    """Which strategies this pass should try to break, least-tested first.

    A strategy nobody has attacked is where an attack is worth most, so the one
    with the fewest recorded trials goes first. Only alphas that have actually
    produced a result are eligible: there is nothing to falsify about a rule that
    has never been priced.
    """
    book = [row for row in store.alpha_book() if row.get("alpha")]
    if not book:
        return []
    ranked = sorted(book, key=lambda r: (store.trial_count(str(r["alpha"])),
                                         -(r.get("runs") or 0)))
    return [str(r["alpha"]) for r in ranked[: max(1, want)]]


def falsify_brief(alpha: str, net: Any, trials: int) -> str:
    """What an unattended pass is told when its job is to break something.

    Written to be hard to turn into good news. The instruction that matters most is
    the last one: a single bad replay is a result for that configuration and not a
    verdict on the idea, and a pass that writes "momentum does not work here" after
    one losing window has produced a sentence somebody will quote for months.
    """
    had = f"It last returned {net:+.2%}. " if isinstance(net, (int, float)) else ""
    return f"""You are running unattended. Nobody is watching and nobody can answer
a question, so do not ask one -- work with what the project already holds.

Your job is to attack `{alpha}`, not to improve it. {had}It has {trials} recorded
trial(s) so far.

Take the settings it already has and replay it under conditions it was not chosen
under. Vary one thing at a time so each result means something:

  * a different window -- earlier, later, shorter
  * a different universe, if the project has one
  * a wider rebalance gap, and a narrower one
  * higher fees and slippage than the project assumes

Use `GET /api/backtest/conditions` to see what the data can actually cover, then
`POST /api/backtest` for each variation.

**Record every trial.** After each replay, POST to /api/trials with its `run_id`, a
one-line `hypothesis` saying what you varied, and `parent_run_id` pointing at the
run it varied. Set `disposition` to `refuted` only when a replay genuinely
contradicts the strategy's claim, and `shelved` when it merely did worse. This is
the part that matters: the attempts are the count that makes the surviving number
mean anything, and nothing else writes them down.

**Do not change the strategy.** Do not edit its step, retune its lookback, or add a
new alpha. You are testing whether what is already believed holds up, and a pass
that improves the thing it was measuring has measured nothing.

**Do not conclude more than you found.** One losing window is a result for that
window. It is not evidence the idea is wrong, and writing it up as though it were
puts a sentence into this project that will be quoted back for months after the
reason turns out to have been a typo in a date. If the strategy survived
everything you tried, say that plainly -- it is the more useful finding and the
one people forget to report.

Finish with two sentences: what you tried, and what it did to your confidence."""


def monitor_brief(alpha: str) -> str:
    """The other safe goal: is the live thing still doing what it promised."""
    return f"""You are running unattended. Nobody is watching and nobody can answer
a question, so do not ask one.

`{alpha}` is being scored forward. Compare what it has earned since the frontier
against what its backtest implied, using `GET /api/live` and `GET /api/backtests`.

Report the gap, not a verdict. Forward periods are few and noisy, so a shortfall
over a handful of rebalances is a thing to note rather than a conclusion to draw.
Do not change anything. Do not run new replays unless you need one to make the
comparison, and record it via /api/trials if you do.

Finish with two sentences: what it promised, and what it has actually done."""


class Pass:
    """One unattended pass, and what it cost.

    Held in its own slot rather than the console's, so a question asked by a person
    while this is running neither kills it nor is killed by it.
    """

    def __init__(self, goal: str, targets: list[str]) -> None:
        self.goal = goal
        self.targets = targets
        self.session_id = str(uuid.uuid4())
        self.asks: list[Any] = []
        self.done = False
        self.error = ""
        self.spent = 0.0
        self.stopped = False

    def state(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "targets": self.targets,
            "session_id": self.session_id,
            "done": self.done,
            "error": self.error,
            "spent": round(self.spent, 4),
            "asks": [a.state() for a in self.asks],
        }

    def stop(self) -> bool:
        self.stopped = True
        killed = False
        for a in self.asks:
            if a.stop():
                killed = True
        return killed


def run_pass(store: Store, project: Project, root: Path, base: str,
             pass_: Pass, prefer: str = "", timeout: float = 1800.0) -> None:
    """Work through the targets, one agent run each, until the budget is gone.

    The budget is measured rather than estimated: the CLI reports what each run
    cost, so a pass that turns out to be expensive stops on the way through
    instead of after.
    """
    from qanat.headless import Ask
    from qanat.headless import run as run_ask

    cfg = project.research
    budget = float(getattr(cfg, "budget_usd", 1.0) or 1.0)
    store.open_session(pass_.session_id, cli="", title=f"{pass_.goal} pass")

    for i, alpha in enumerate(pass_.targets):
        if pass_.stopped:
            break
        if pass_.spent >= budget:
            store.event("info", "research",
                        f"stopped after {alpha}: spent ${pass_.spent:.2f} of "
                        f"${budget:.2f}")
            break
        row = next((r for r in store.alpha_book() if r.get("alpha") == alpha), {})
        brief = (falsify_brief(alpha, row.get("last_net"), store.trial_count(alpha))
                 if pass_.goal == "falsify" else monitor_brief(alpha))
        ask = Ask(question=brief)
        pass_.asks.append(ask)
        try:
            run_ask(ask, root, base, timeout=timeout, prefer=prefer,
                    session_id=pass_.session_id, resume=i > 0)
        except Exception as exc:  # noqa: BLE001
            ask.error = f"{type(exc).__name__}: {exc}"
            ask.done = True
        pass_.spent += float(ask.cost_usd or 0.0)
        store.note_ask(pass_.session_id, cost_usd=ask.cost_usd, model=ask.model,
                       title=f"{pass_.goal} · {alpha}")
        store.save_ask(pass_.session_id, ask.state())
        store.event("error" if ask.error else "info", "research",
                    f"{pass_.goal} · {alpha}: "
                    + (ask.error[:120] if ask.error else (ask.answer[:120] or "done")))

    pass_.done = True
    #  Summarised like any other session, and by the same route: it resumed the
    #  conversation to ask what happened, so the row a person reads tomorrow was
    #  written by the thing that was there.
    try:
        from qanat.headless import summarise

        store.end_session(pass_.session_id, summarise(pass_.session_id, root, prefer))
    except Exception:  # noqa: BLE001 -- tidying must not fail the pass
        store.end_session(pass_.session_id, "")


def start(state: Any, goal: str = "", base: str = "") -> Pass | None:
    """Fire a pass in the background, unless one is already running.

    Returns None when there is nothing to work on -- a project with no priced
    alpha has nothing to falsify, and a pass that woke up to find that should say
    so rather than inventing something to do.
    """
    from qanat.headless import _agent_pref

    running = getattr(state, "_research", None)
    if running is not None and not running.done:
        return running
    cfg = state.project.research
    want = int(getattr(cfg, "targets", 1) or 1)
    goal = goal or str(getattr(cfg, "goal", "falsify") or "falsify")
    targets = target_alphas(state.store, state.project, want)
    if not targets:
        state.store.event("warn", "research",
                          "nothing to work on: no alpha in this project has been priced yet")
        return None

    pass_ = Pass(goal, targets)
    state._research = pass_
    state.store.event("info", "research", f"{goal} pass on {', '.join(targets)}")

    def work() -> None:
        from qanat.store import set_actor

        set_actor("research")
        run_pass(state.store, state.project, state.root,
                 base or "http://127.0.0.1:8420", pass_, _agent_pref(state))

    threading.Thread(target=work, daemon=True).start()
    return pass_
