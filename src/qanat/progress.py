"""What a screen is doing right now, while it is doing it.

A backtest can run for a while, and until it finishes there is nothing to show --
the report only exists at the end. This is the in-between: which as-of date the
screen is on, and which job just finished writing which table.

It records the jobs in the order they actually ran, which is the dependency order
observed rather than inferred from the shape of the graph.

Nothing reads it yet. `run_backtest` fills it on every screen and `qanat backtest`
prints its own line as it goes, so the reader this was built for -- a tool that
answers "what is the screen doing right now" -- is the obvious next use and is not
written. It is kept because the recording is the hard half and it already works.

Kept in memory on purpose. Only the process holding the store open can run a
screen, so there is nothing here a second process should be able to see.
"""

from __future__ import annotations

import threading
from typing import Any

_LOCK = threading.RLock()
_STATE: dict[str, Any] = {
    "running": False,
    "run_id": None,
    "stop": None,
    "stops_done": 0,
    "stops_total": 0,
    "seq": 0,        # bumps on every job, so a reader can tell "new" from "same"
    "jobs": [],      # the jobs of the current pass, oldest first
    # Every pass, with the jobs it ran. A pass over a small project finishes in
    # less time than one poll, so reporting only the current one meant a reader
    # saw roughly every fourth and missed the rest. Keeping the list means each
    # pass that actually ran can be accounted for.
    "passes": [],
    "jobs_in_run": [],   # the jobs this screen touches at all
    "periods": [],   # every period closed so far -- the curve, while it is drawn
    "totals": {},    # the run so far, recomputed each time a period closes
    "segments": {},  # the same, cut into in-sample and out-of-sample
    "conditions": {},
    "finished": None,
}


def start(run_id: int, stops_total: int, conditions: dict[str, Any] | None = None,
          jobs_in_run: list[str] | None = None) -> None:
    with _LOCK:
        _STATE.update(running=True, run_id=run_id, stops_total=stops_total, stops_done=0,
                      stop=None, jobs=[], passes=[], jobs_in_run=list(jobs_in_run or []),
                      periods=[], totals={}, segments={},
                      conditions=dict(conditions or {}), finished=None)
        _STATE["seq"] += 1


# How many passes are kept. A screen of a few hundred rebalances is normal;
# keeping them all costs nothing worth counting, but the cap stops a very long run
# from growing without limit.
_KEEP = 600


def stop_at(stop: str, done: int) -> None:
    """A new as-of date. The pass starts over from the left."""
    with _LOCK:
        _STATE.update(stop=stop, stops_done=done, jobs=[])
        _STATE["passes"].append({"stop": stop, "no": done + 1, "jobs": []})
        del _STATE["passes"][:-_KEEP]
        _STATE["seq"] += 1


def job(job_id: str, status: str, rows: int, targets: list[str]) -> None:
    with _LOCK:
        row = {"job": job_id, "status": status, "rows": rows, "wrote": list(targets)}
        _STATE["jobs"].append(row)
        if _STATE["passes"]:
            _STATE["passes"][-1]["jobs"].append(row)
        _STATE["seq"] += 1


def period(period_row: dict[str, Any], totals: dict[str, Any],
           segments: dict[str, Any] | None = None) -> None:
    """One holding period closed. This is the point of scoring as we go: a new
    point on the curve is available now rather than a whole curve much later."""
    with _LOCK:
        _STATE["periods"].append(period_row)
        _STATE["totals"] = dict(totals)
        _STATE["segments"] = dict(segments or {})
        _STATE["seq"] += 1


def failed(stop: str) -> None:
    with _LOCK:
        row = {"job": "(pass failed)", "status": "failed", "rows": 0,
               "wrote": [], "stop": stop}
        _STATE["jobs"].append(row)
        if _STATE["passes"]:
            _STATE["passes"][-1]["jobs"].append(row)
        _STATE["seq"] += 1


def finish(run_id: int, status: str) -> None:
    with _LOCK:
        _STATE.update(running=False, finished={"run_id": run_id, "status": status})
        _STATE["seq"] += 1


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return {
            "running": _STATE["running"],
            "run_id": _STATE["run_id"],
            "stop": _STATE["stop"],
            "stops_done": _STATE["stops_done"],
            "stops_total": _STATE["stops_total"],
            "seq": _STATE["seq"],
            "jobs": list(_STATE["jobs"]),
            "passes": [dict(p, jobs=list(p["jobs"])) for p in _STATE["passes"]],
            "jobs_in_run": list(_STATE["jobs_in_run"]),
            "periods": list(_STATE["periods"]),
            "totals": dict(_STATE["totals"]),
            "segments": dict(_STATE["segments"]),
            "conditions": dict(_STATE["conditions"]),
            "finished": _STATE["finished"],
        }


__all__ = ["failed", "finish", "job", "period", "snapshot", "start", "stop_at"]
