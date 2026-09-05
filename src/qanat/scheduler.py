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

from croniter import croniter

from qanat.models import Project, Source
from qanat.project_io import save_project
from qanat.runner import RunResult, run_source, run_step
from qanat.store import Store

#: the job id a live replay reports under, so it shows in the log like any other
_LIVE = "backtest"


class Scheduler:
    def __init__(self, store: Store, project: Project, root: Path, workers: int = 4):
        self.store = store
        self.project = project
        self.root = root
        self.workers = workers
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self.next_at: dict[str, datetime] = {}
        self._last_retention = 0.0
        self._last_live = 0.0
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
            if self.project.retention and time.time() - self._last_retention >= 60:
                self._last_retention = time.time()
                from qanat.retention import run_retention

                run_retention(self.store, self.project)
            if time.time() - self._last_live >= 30:
                self._last_live = time.time()
                self.score_forward()
            self._stop.wait(1.0)

    def score_forward(self) -> bool:
        """Run a live pass if the data has reached the next rebalance date.

        Started in its own thread and guarded like any other job, so a replay
        that outlives the gap between checks is not started twice.
        """
        from qanat.backtest import live_window

        if self.project.backtest is None or not self.project.backtest.live:
            return False
        with self._lock:
            if _LIVE in self._inflight:
                return False
        try:
            window = live_window(self.store, self.project)
        except Exception as exc:  # noqa: BLE001 -- a bad window must not stop the loop
            self.store.event("error", _LIVE, f"{type(exc).__name__}: {exc}")
            return False
        if window is None:
            return False
        with self._lock:
            self._inflight.add(_LIVE)
        threading.Thread(target=self._score, args=window, daemon=True).start()
        return True

    def note_frontier(self, to: str) -> bool:
        """Write down where the data ended when live scoring began. Once, ever.

        Everything after this date is a period nobody could see when the alpha was
        chosen. Worked out fresh on each pass it would move forward every day, and
        the live segment would collapse back into out of sample.

        It goes in the file rather than the store because a replay rebuilds the
        store, and the frontier has to outlive that. Returns whether it wrote.
        """
        bt = self.project.backtest
        if bt is None or bt.live_from:
            return False
        bt.live_from = to
        save_project(self.project, self.root)
        self.store.event("info", _LIVE, f"live from {to} · periods after it were "
                                        "not on disk when the alpha was chosen")
        return True

    def _score(self, frm: str, to: str) -> None:
        from qanat.backtest import run_backtest

        try:
            self.note_frontier(to)
            self.store.event("info", _LIVE, f"scoring {frm} to {to}")
            run_backtest(self.store, self.project, self.root, frm, to, live=True)
        except Exception as exc:  # noqa: BLE001 -- same
            self.store.event("error", _LIVE, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._inflight.discard(_LIVE)

    def fire(self, job_id: str) -> None:
        """Run a job now, in its own thread, unless it is already running."""
        with self._lock:
            if job_id in self._inflight:
                self.store.event("warn", job_id, "still running -- this tick was skipped")
                return
            if len(self._inflight) >= self.workers:
                self.store.event("warn", job_id, "all workers busy -- this tick was skipped")
                return
            self._inflight.add(job_id)
        threading.Thread(target=self._execute, args=(job_id,), daemon=True).start()

    def _execute(self, job_id: str) -> RunResult | None:
        result: RunResult | None = None
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
            if result is not None and result.ok and result.rows:
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

    # ---- state for the console ------------------------------------------------
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
        # a step on no clock still runs by itself, and the console should say so
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
