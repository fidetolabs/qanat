"""What starts a job, in one background thread.

Deliberately small. The loop wakes once a second and fires whatever schedule row
says it is due. A definition already running is never started twice.

**A schedule is a row now, not a field.** So a definition may carry a daily and
a weekly without being copied, and disabling one disturbs neither the others nor
the definition. The clock walks schedules; the definition is what each one
points at.

**It deletes nothing.** The loop used to apply every `retention:` policy once a
minute, which made removal a side effect of the clock running -- a row that had
been served went away with nothing asking for it and nothing saying so. Retention
is still declared in the project and is still real; it is applied by the `plan`
tool's `prune` flag, which lists what it will remove before it removes it.

A transform may also wait on a table instead of a clock. When a job succeeds
having written rows, every transform waiting on one of those tables runs, and
when those finish the same thing happens again, on down the graph. A clock is a
guess about when the data arrives; this is the arrival itself.

Ingestion jobs stay on a clock either way. One waits on something outside qanat,
so the only way to find out is to ask.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

from qanat.models import IngestionJobDefinition, JobSchedule, Project
from qanat.runner import RunResult, run_ingestion, run_transform
from qanat.store import Store, set_actor


class Scheduler:
    def __init__(self, store: Store, project: Project, root: Path, workers: int = 4):
        self.store = store
        self.project = project
        self.root = root
        self.workers = workers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: definition id -> nothing. A definition is what holds a worker, because
        #: two schedules over one definition firing at once would be the same
        #: work twice.
        self._inflight: set[str] = set()
        #: definition id -> the moment it stops getting a worker. One with no
        #: limit is absent from this and holds its slot until the process ends.
        self._deadline: dict[str, float] = {}
        self._lock = threading.Lock()
        #: schedule id -> when that schedule next fires. Keyed on the schedule,
        #: not the definition: that is what lets one definition have two.
        self.next_at: dict[str, datetime] = {}
        #  Where the unattended pass reaches the project, and the state object it
        #  drives. Both are handed over by `serve`, which is the only caller that
        #  has an API to point at -- so a bare Scheduler simply never researches.
        self._rebuild()

    def _rebuild(self) -> None:
        """Every enabled schedule whose definition exists, and when each is next due."""
        known = {d.id for d in self.project.definitions}
        self._schedules: list[JobSchedule] = [
            s for s in self.project.job_schedules if s.enabled and s.definition in known
        ]
        now = datetime.now(timezone.utc)
        self.next_at = {s.id: croniter(s.cron, now).get_next(datetime) for s in self._schedules}

    def reload(self, project: Project) -> None:
        """Pick up a changed project without restarting the server."""
        self.project = project
        with self._lock:
            running = set(self._inflight)
        self._rebuild()
        self.store.event("info", "scheduler", f"reloaded · {len(self._schedules)} schedule(s)")
        if running:
            self.store.event("warn", "scheduler",
                             f"{len(running)} job(s) still running from before reload")

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="qanat-scheduler", daemon=True)
        self._thread.start()
        self.store.event("info", "scheduler", f"started with {len(self._schedules)} schedule(s)")

    def quiet(self, timeout: float = 300.0) -> bool:
        """Block until nothing is running. False if it was still busy at timeout.

        The default is generous on purpose: a live pass is a whole screen, and a
        slow CI runner takes several minutes over what takes seconds here.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if not self._inflight:
                    return True
            time.sleep(0.02)
        return False

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None

    # ---- the loop -------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            now = datetime.now(timezone.utc)
            for schedule in self._schedules:
                due = self.next_at.get(schedule.id)
                if due and now >= due:
                    self.next_at[schedule.id] = croniter(schedule.cron, now).get_next(datetime)
                    # The instant it was *for*, which is what groups the attempts
                    # at it into one job.
                    self.fire(schedule.definition, scheduled_for=due, schedule_id=schedule.id)
            self.reap()
            self._stop.wait(1.0)

    def limit(self, definition_id: str) -> float | None:
        """How long this definition gets, in seconds, or None for as long as it takes."""
        from qanat.retention import parse_duration

        definition = self.project.definition(definition_id)
        text = getattr(definition, "timeout", None) or self.project.job_timeout
        if not text:
            return None
        try:
            return parse_duration(text, "timeout").total_seconds()
        except ValueError:
            return None

    def reap(self) -> list[str]:
        """Stop waiting on jobs that have run past their limit.

        A job used to hold its worker until the process ended, so four slow ones
        stopped the whole scheduler with nothing but warn events to say so. Python
        cannot kill a running thread, so this frees the slot and closes the
        execution row rather than pretending the work stopped -- the thread
        finishes on its own and finds its slot already gone, which is harmless.
        What it buys is a scheduler that keeps working, and an execution row that
        stops saying `running` for ever.
        """
        now = time.time()
        with self._lock:
            over = [j for j, at in self._deadline.items() if at <= now and j in self._inflight]
            for did in over:
                self._inflight.discard(did)
                self._deadline.pop(did, None)
        for did in over:
            self.store.event("warn", did, (
                "over its timeout -- the worker was freed. The job may still be running; "
                "nothing here can stop a Python thread, so raise the timeout or make the "
                "transform finish sooner"
            ))
            self._close_open_execution(did, "timeout", "the job ran past its timeout")
        return over

    def cancel(self, definition_id: str) -> bool:
        """Stop waiting on a job now. Same limits as `reap`."""
        with self._lock:
            if definition_id not in self._inflight:
                return False
            self._inflight.discard(definition_id)
            self._deadline.pop(definition_id, None)
        self.store.event("warn", definition_id, "cancelled -- the worker was freed")
        self._close_open_execution(definition_id, "cancelled", "cancelled")
        return True

    def _close_open_execution(self, definition_id: str, status: str, why: str) -> None:
        # Drained, like every scalar read on the store: a one-row fetch leaves
        # the result open and DuckDB 2.0 then refuses to register a frame on that
        # connection -- and `rcon` is the writing connection wherever cursors are
        # unavailable, so a read here can break the next write.
        from qanat.store import EXECUTIONS

        row = self.store._one(
            f"SELECT execution_id FROM {EXECUTIONS} WHERE definition_id = ? "
            f"AND status = 'running' ORDER BY started_at DESC LIMIT 1",
            [definition_id], con=self.store.rcon)
        if row:
            self.store.end_execution(row[0], status, 0, why)

    def fire(self, definition_id: str, actor: str = "schedule",
             scheduled_for: datetime | None = None, schedule_id: str | None = None) -> None:
        """Run a definition now, in its own thread, unless it is already running.

        `actor` is who asked. It rides into the worker thread because a
        ContextVar does not cross one, and the event log carries it: a job that
        ran because an agent asked reads differently from one the clock fired.
        """
        with self._lock:
            if definition_id in self._inflight:
                self.store.event("warn", definition_id, "still running -- this tick was skipped")
                return
            if len(self._inflight) >= self.workers:
                self.store.event("warn", definition_id,
                                 "all workers busy -- this tick was skipped")
                return
            self._inflight.add(definition_id)
            secs = self.limit(definition_id)
            if secs:
                self._deadline[definition_id] = time.time() + secs
        threading.Thread(
            target=self._execute,
            args=(definition_id, actor, scheduled_for, schedule_id),
            daemon=True,
        ).start()

    def _execute(self, definition_id: str, actor: str = "schedule",
                 scheduled_for: datetime | None = None,
                 schedule_id: str | None = None) -> RunResult | None:
        result: RunResult | None = None
        set_actor(actor)          # a fresh thread starts on the default
        when = None if scheduled_for is None else scheduled_for.replace(tzinfo=None)
        try:
            definition = self.project.definition(definition_id)
            if definition is None:
                return None
            if isinstance(definition, IngestionJobDefinition):
                result = run_ingestion(self.store, self.project, self.root, definition,
                                       scheduled_for=when, schedule_id=schedule_id)
            else:
                result = run_transform(self.store, self.project, self.root, definition,
                                       scheduled_for=when, schedule_id=schedule_id)
            return result
        finally:
            # Out of the in-flight set before waking anything, or a transform that
            # feeds itself further down the graph would look busy to its own wake.
            with self._lock:
                self._inflight.discard(definition_id)
            # On success, not on rows. A transform that correctly computes nothing
            # clears its table, and waking only on rows > 0 meant the transforms
            # waiting on it kept yesterday's answer indefinitely while every job
            # showed green.
            if result is not None and result.ok:
                self.wake(result.targets)

    def wake(self, written: Sequence[str]) -> list[str]:
        """Run every transform waiting on one of these tables. Returns what it started.

        Nothing here has to walk the graph. Each woken transform wakes whatever
        waits on *its* tables when it finishes, so the chain follows the edges by
        itself, and the stage contract forbids a cycle for it to get stuck in.
        """
        touched = set(written)
        woken = []
        for transform in self.project.waiting_on(written):
            woken.append(transform.id)
            self.store.event("info", transform.id,
                             f"woken by {', '.join(sorted(touched & set(transform.when)))}")
            self.fire(transform.id)
        return woken

    # ---- what is scheduled, and what is running right now ----------------------
    def status(self) -> dict[str, dict[str, object]]:
        """One entry per schedule, plus the transforms that run on arrival instead.

        Keyed on the schedule rather than on the definition, because a definition
        with a daily and a weekly has two answers to "when next" and a dict keyed
        on it could only hold one.
        """
        with self._lock:
            inflight = set(self._inflight)
        out: dict[str, dict[str, object]] = {
            s.id: {
                "definition": s.definition,
                "cron": s.cron,
                "enabled": s.enabled,
                "next_at": self.next_at[s.id].isoformat(sep=" ", timespec="seconds"),
                "running": s.definition in inflight,
            }
            for s in self._schedules
        }
        # a transform on no clock still runs by itself, and this should say so
        for transform in self.project.transform_job_definitions:
            if transform.when and transform.id not in out:
                out[transform.id] = {"definition": transform.id, "cron": None,
                                     "enabled": True, "when": list(transform.when),
                                     "next_at": None,
                                     "running": transform.id in inflight}
        return out

    def by_definition(self) -> dict[str, list[dict[str, object]]]:
        """Every schedule, grouped by the definition it fires.

        What a caller asking *"is anything running my subscription?"* wants, and
        the shape `status()` cannot have now that several schedules may name one
        definition.
        """
        out: dict[str, list[dict[str, object]]] = {}
        for sid, row in self.status().items():
            out.setdefault(str(row["definition"]), []).append({"schedule": sid, **row})
        return out


def sleep_forever() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
