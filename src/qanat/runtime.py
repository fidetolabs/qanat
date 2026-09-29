"""What one running process holds: the store, the project, and the scheduler.

This used to live in `api.py`, next to the HTTP surface the console drew. The
console is gone and so is that surface, but the thing itself is still needed --
by the scheduler, and by the unattended pass in `research.py` -- so it lives here
on its own rather than inside a transport.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qanat.models import Project
from qanat.project import load
from qanat.scheduler import Scheduler
from qanat.store import Store


@dataclass
class AppState:
    """One project, open, with whatever is running against it."""

    store: Store
    project: Project
    root: Path
    sched: Scheduler | None
    _lock: threading.RLock = field(default_factory=threading.RLock)
    #: Held for the whole of a replay, and by nothing else. The state lock guards
    #: edits to the project, which a replay never makes, so a backtest must not
    #: take it.
    _replay: threading.Lock = field(default_factory=threading.Lock)
    #: The unattended pass, while one is running.
    _research: Any = None

    def reload(self) -> None:
        with self._lock:
            self.project, self.root = load(self.root)
            if self.sched:
                self.sched.reload(self.project)

    def set_project(self, project: Project) -> None:
        with self._lock:
            self.project = project
            if self.sched:
                self.sched.reload(project)


__all__ = ["AppState"]
