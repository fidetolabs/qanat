"""What starts a job, in one background thread.

Deliberately small. The loop wakes once a second and runs whatever its cron line
says is due. A job that is already running is never started twice.

A step may also wait on a table instead of a clock. When a job succeeds having
written rows, every step waiting on one of those tables runs, and when those
finish the same thing happens again, on down the graph. A clock is a guess about
when the data arrives; this is the arrival itself.

Sources stay on a clock either way. A source waits on something outside qanat,
so the only way to find out is to ask.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

from qanat.models import Project, Source
from qanat.project_io import save_project
from qanat.runner import RunResult, run_source, run_step
from qanat.store import Store, set_actor

class Scheduler:
    def __init__(self, store: Store, project: Project, root: Path, workers: int = 4):
        self.store = store
        self.project = project
        self.root = root
        self.workers = workers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inflight: set[str] = set()
        #: job id -> the moment it stops getting a worker. A job with no limit is
        #: absent from this and holds its slot until the process ends.
        self._deadline: dict[str, float] = {}
        self._lock = threading.Lock()
        self.next_at: dict[str, datetime] = {}
        self._last_retention = 0.0
        #  Where the unattended pass reaches the project, and the state object it
        #  drives. Both are handed over by `serve`, which is the only caller that
        #  has an API to point at -- so a bare Scheduler simply never researches.
        self._rebuild_jobs()

    def _rebuild_jobs(self) -> None:
        self._jobs = [j for j in self.project.jobs if j.schedule]
        now = datetime.now(timezone.utc)
        self.next_at = {}
        for j in self._jobs:
            self.next_at[j.id] = croniter(j.schedule, now).get_next(datetime)

    def reload(self, project: Project) -> None:
        """Pick up a new qanat.yaml without restarting the server."""
        self.project = project
        with self._lock:
            running = set(self._inflight)
        self._rebuild_jobs()
        self.store.event("info", "scheduler", f"reloaded · {len(self._jobs)} scheduled job(s)")
        if running:
            self.store.event("warn", "scheduler", f"{len(running)} job(s) still running from before reload")

    # ---- lifecycle ------------------------------------------------------------
    def start(self) -> None:
        if self._thread:
            return
        self._thread = threading.Thread(target=self._loop, name="qanat-scheduler", daemon=True)
        self._thread.start()
        self.store.event("info", "scheduler", f"started with {len(self._jobs)} scheduled job(s)")

    def quiet(self, timeout: float = 300.0) -> bool:
        """Block until nothing is running. False if it was still busy at timeout.

        The default is generous on purpose: a live pass is a whole replay, and a
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
            for job in self._jobs:
                due = self.next_at.get(job.id)
                if due and now >= due:
                    self.next_at[job.id] = croniter(job.schedule, now).get_next(datetime)
                    self.fire(job.id)
            self.reap()
            if self.project.retention and time.time() - self._last_retention >= 60:
                self._last_retention = time.time()
                from qanat.retention import run_retention

                run_retention(self.store, self.project)
            self._stop.wait(1.0)

    def limit(self, job_id: str) -> float | None:
        """How long this job gets, in seconds, or None for as long as it takes."""
        from qanat.retention import parse_duration

        job = self.project.job(job_id)
        text = getattr(job, "timeout", None) or self.project.job_timeout
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
        cannot kill a running thread, so this frees the slot and closes the run row
        rather than pretending the work stopped -- the thread finishes on its own and
        finds its slot already gone, which is harmless. What it buys is a scheduler
        that keeps working, and a run row that stops saying `running` for ever.
        """
        now = time.time()
        with self._lock:
            over = [j for j, at in self._deadline.items() if at <= now and j in self._inflight]
            for job_id in over:
                self._inflight.discard(job_id)
                self._deadline.pop(job_id, None)
        for job_id in over:
            self.store.event("warn", job_id, (
                "over its timeout -- the worker was freed. The job may still be running; "
                "nothing here can stop a Python thread, so raise the timeout or make the "
                "step finish sooner"
            ))
            self._close_open_run(job_id, "timeout", "the job ran past its timeout")
        return over

    def cancel(self, job_id: str) -> bool:
        """Stop waiting on a job now. Same limits as `reap`."""
        with self._lock:
            if job_id not in self._inflight:
                return False
            self._inflight.discard(job_id)
            self._deadline.pop(job_id, None)
        self.store.event("warn", job_id, "cancelled -- the worker was freed")
        self._close_open_run(job_id, "cancelled", "cancelled")
        return True

    def _close_open_run(self, job_id: str, status: str, why: str) -> None:
        row = self.store.rcon.execute(
            "SELECT run_id FROM _qanat_runs WHERE job_id = ? AND status = 'running' "
            "ORDER BY started_at DESC LIMIT 1", [job_id]
        ).fetchone()
        if row:
            self.store.end_run(row[0], status, 0, why)

    def fire(self, job_id: str, actor: str = "schedule") -> None:
        """Run a job now, in its own thread, unless it is already running.

        `actor` is who asked. It rides into the worker thread because a
        ContextVar does not cross one, and the event log carries it: a job that
        ran because an agent asked reads differently from one the clock fired.
        """
        with self._lock:
            if job_id in self._inflight:
                self.store.event("warn", job_id, "still running -- this tick was skipped")
                return
            if len(self._inflight) >= self.workers:
                self.store.event("warn", job_id, "all workers busy -- this tick was skipped")
                return
            self._inflight.add(job_id)
            secs = self.limit(job_id)
            if secs:
                self._deadline[job_id] = time.time() + secs
        threading.Thread(target=self._execute, args=(job_id, actor), daemon=True).start()

    def _execute(self, job_id: str, actor: str = "schedule") -> RunResult | None:
        result: RunResult | None = None
        set_actor(actor)          # a fresh thread starts on the default
        try:
            job = self.project.job(job_id)
            if job is None:
                return None
            if isinstance(job, Source):
                result = run_source(self.store, self.project, self.root, job)
            else:
                result = run_step(self.store, self.project, self.root, job)
            return result
        finally:
            # Out of the in-flight set before waking anything, or a step that
            # feeds itself further down the graph would look busy to its own wake.
            with self._lock:
                self._inflight.discard(job_id)
            # On success, not on rows. A step that correctly computes nothing clears
            # its table, and waking only on rows > 0 meant the steps waiting on it
            # kept yesterday's answer indefinitely while every job showed green.
            if result is not None and result.ok:
                self.wake(result.targets)

    def wake(self, written: Sequence[str]) -> list[str]:
        """Run every step waiting on one of these tables. Returns what it started.

        Nothing here has to walk the graph. Each woken step wakes whatever waits
        on *its* tables when it finishes, so the chain follows the edges by
        itself, and the stage contract forbids a cycle for it to get stuck in.
        """
        touched = set(written)
        woken = []
        for step in self.project.waiting_on(written):
            woken.append(step.id)
            self.store.event("info", step.id,
                             f"woken by {', '.join(sorted(touched & set(step.when)))}")
            self.fire(step.id)
        return woken

    # ---- what is scheduled, and what is running right now ----------------------
    def status(self) -> dict[str, dict[str, object]]:
        with self._lock:
            inflight = set(self._inflight)
        out: dict[str, dict[str, object]] = {
            j.id: {
                "schedule": j.schedule,
                "when": list(getattr(j, "when", [])),
                "next_at": self.next_at[j.id].isoformat(sep=" ", timespec="seconds"),
                "running": j.id in inflight,
            }
            for j in self._jobs
        }
        # a step on no clock still runs by itself, and this should say so
        for step in self.project.steps:
            if step.when and step.id not in out:
                out[step.id] = {"schedule": None, "when": list(step.when),
                                "next_at": None, "running": step.id in inflight}
        return out


def sleep_forever() -> None:
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
