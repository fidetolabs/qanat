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
from qanat.store import Store, set_actor

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
        #: job id -> the moment it stops getting a worker. A job with no limit is
        #: absent from this and holds its slot until the process ends.
        self._deadline: dict[str, float] = {}
        self._lock = threading.Lock()
        self.next_at: dict[str, datetime] = {}
        self._last_retention = 0.0
        self._last_live = 0.0
        #: Set when live is on but cannot be run as configured. A misconfiguration
        #: does not get better by being retried every thirty seconds, and the log
        #: line it writes each time buries everything else. Cleared on reload,
        #: because editing the file is how it gets fixed.
        self._live_halt = ""
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
        self._live_halt = ""          # the file changed; give live another go
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
            if time.time() - self._last_live >= 30:
                self._last_live = time.time()
                self.score_forward()
            self._stop.wait(1.0)

    def live_alphas(self) -> list[str] | None:
        """Which alphas a live pass prices, or None when nothing can say.

        One alpha in the project needs no instruction. Several, and the choice is
        not ours: pricing two together is a third strategy, and picking one of them
        silently would report a number for a portfolio nobody asked to hold.
        """
        bt = self.project.backtest
        if bt is None:
            return None
        if bt.live_alphas:
            return list(bt.live_alphas)
        book = [a for a, _ in self.project.alphas]
        return [book[0]] if len(book) == 1 else None

    def score_forward(self) -> bool:
        """Run a live pass if the data has reached the next rebalance date.

        Started in its own thread and guarded like any other job, so a replay
        that outlives the gap between checks is not started twice.
        """
        from qanat.backtest import live_window

        if self.project.backtest is None or not self.project.backtest.live:
            return False
        if self._live_halt:
            return False
        with self._lock:
            if _LIVE in self._inflight:
                return False
        # Asked before the window, because a project that cannot say which alpha to
        # price is not going to be able to price one when the next date arrives
        # either. This used to be discovered inside `run_backtest`, which raised,
        # which was logged and swallowed -- on a loop, forever, while the console
        # went on reporting that everything was fine.
        alpha = self.live_alphas()
        if alpha is None:
            book = [a for a, _ in self.project.alphas]
            self._live_halt = "no alpha named"
            self.store.event(
                "error", _LIVE,
                f"live is on, but this project has {len(book)} alphas and none is named to "
                f"price: {', '.join(book)}. Set `live_alphas:` in the backtest block to one "
                f"of them (or several, to hold them as one book). Nothing is being scored "
                f"until you do.",
            )
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
        threading.Thread(target=self._score, args=(*window, alpha), daemon=True).start()
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

    def _score(self, frm: str, to: str, alpha: list[str]) -> None:
        from qanat.backtest import run_backtest

        try:
            self.store.event("info", _LIVE, f"scoring {frm} to {to} · {', '.join(alpha)}")
            run_backtest(self.store, self.project, self.root, frm, to, alpha=alpha, live=True)
            # Stamped only once a pass has landed. Written before the run, a run that
            # then failed still moved the frontier -- and `note_frontier` writes once
            # and never again, so the date defining out-of-sample was left pointing at
            # a moment nothing had ever been scored at, permanently, with nothing in
            # the file to say it was wrong.
            self.note_frontier(to)
        except Exception as exc:  # noqa: BLE001 -- same
            self.store.event("error", _LIVE, f"{type(exc).__name__}: {exc}")
        finally:
            with self._lock:
                self._inflight.discard(_LIVE)

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
        that keeps working, and a console that stops showing the job as running.
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
        self._close_open_run(job_id, "cancelled", "cancelled from the console")
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
        ContextVar does not cross one, and the console shows it: a job that ran
        because an agent asked reads differently from one the clock fired.
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
