"""What one running process holds: the store, the project, and the scheduler.

It lives on its own rather than inside a transport, because more than one thing
needs it: `qanat mcp --http --schedule` builds it, the scheduler runs against it, and the
unattended pass in `research.py` reads it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

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
    #: Held for the whole of a screen, and by nothing else. The state lock guards
    #: edits to the project, which a screen never makes, so a screen must not
    #: take it.
    _screen: threading.Lock = field(default_factory=threading.Lock)

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
