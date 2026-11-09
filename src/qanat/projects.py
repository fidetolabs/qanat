"""Projects in one home folder, and the program that holds several of them open.

**A project is a folder in the home folder, created by name.** The home folder is
`~/qanat` unless `QANAT_HOME` or `qanat serve --home` says otherwise. There is no
list of projects to disagree with the disk: the folders that hold a
`qanat.yaml` *are* the list. Nothing here adds a project that lives somewhere
else, and nothing here deletes a folder it did not make.

**Several projects open at once.** Each has its own store, its own jobs and its
own writer lock, so nothing forces one at a time. A project is opened the first
time somebody selects it and stays open until the program stops, holding its
lock (T-11) the whole while -- the CLI is refused on it in the meantime, and the
refusal names this program (T-12).

**Who is working where is held per holder.** The app is one holder; each MCP
session is another. A holder selects one project and every call it makes lands
there, so no call carries a project name a slip could get wrong.

Both doors call this module and nothing else for any of it, which is what keeps
the page and the tools from drifting apart.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

#: The app is one holder. An MCP session is another, keyed by its session id.
APP = "app"

#: Beside the projects, in the home folder. The person's, not the data's.
HOME_FILE = ".qanat-home.yaml"

#: Inside each project. Ignored by git from the inside, so a project committed
#: to a repository never carries a contact address or a clock reading.
LOCAL_DIR = ".qanat"
STATE_FILE = "state.yaml"
SETTINGS_FILE = "settings.yaml"

THEMES = ("fidetolabs-white", "fidetolabs-dark")
DEFAULT_THEME = "fidetolabs-white"

#: A folder name a person can type and a URL can carry without escaping.
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class ProjectsError(ValueError):
    """A refusal a person or an agent can act on. The message says what to do."""


def default_home() -> Path:
    return Path(os.environ.get("QANAT_HOME") or Path.home() / "qanat").expanduser()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------ per-project files
def _local(root: Path) -> Path:
    d = root / LOCAL_DIR
    if not d.is_dir():
        d.mkdir(parents=True, exist_ok=True)
        # Ignored from the inside: the project needs no .gitignore of its own.
        (d / ".gitignore").write_text("*\n")
    return d


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        got = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return {}
    return got if isinstance(got, dict) else {}


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=True))
    tmp.replace(path)


def settings(root: Path) -> dict[str, Any]:
    """What the person set for this project. Today only the SEC contact."""
    return _read_yaml(root / LOCAL_DIR / SETTINGS_FILE)


def save_user_agent(root: Path, user_agent: str) -> dict[str, Any]:
    """Set the contact the SEC connector sends for this project, or clear it.

    **Kept out of `qanat.yaml` and `jobs.yaml`.** A save there rewrites every
    tool-owned file and has dropped a field before; this is its own small file.
    """
    ua = (user_agent or "").strip()
    if ua and "@" not in ua:
        raise ProjectsError(
            f"The contact '{ua}' carries no email address. The SEC asks for one so it can contact "
            "whoever is making the requests: 'Jane Doe jane@example.com'.")
    path = _local(root) / SETTINGS_FILE
    got = _read_yaml(path)
    if ua:
        got["user_agent"] = ua
    else:
        got.pop("user_agent", None)
    _write_yaml(path, got)
    return {"user_agent": got.get("user_agent")}


def project_env(root: Path | None) -> dict[str, str]:
    """What a `${VAR}` in this project's options resolves to before the shell.

    A project's own contact wins over the shell's `SEC_USER_AGENT`, because the
    point of setting one per project is that two projects can send two.
    """
    if root is None:
        return {}
    ua = settings(Path(root)).get("user_agent")
    return {"SEC_USER_AGENT": str(ua)} if ua else {}


def stamp(root: Path, when: str | None = None) -> str:
    """Record that something in the project changed now. Never raises.

    **Only a change stamps it** -- an ingest, a run, a saved metric, filter or
    contact, a job ending. Opening, closing and selecting do not: the list is
    ordered by this, and an order that moved every time somebody looked at a
    project was one nobody could find anything in.
    """
    when = when or _now()
    with contextlib.suppress(OSError):
        path = _local(root) / STATE_FILE
        got = _read_yaml(path)
        # `last_used` is the word before 2026-11-09. It is carried over as the
        # starting point, or an older job time would overwrite a newer stamp.
        have = str(got.get("last_updated") or got.get("last_used") or "")
        if "last_used" in got:
            got["last_updated"] = max(have, str(got.get("last_updated") or ""))
            got.pop("last_used")
        if have < when:
            got["last_updated"] = when
        _write_yaml(path, got)
    return when


def last_updated(root: Path) -> str | None:
    got = _read_yaml(root / LOCAL_DIR / STATE_FILE)
    when = got.get("last_updated") or got.get("last_used")
    return str(when) if when else None


def created_at(root: Path) -> str | None:
    """When the project was made: stamped at create, else when its file was made."""
    got = _read_yaml(root / LOCAL_DIR / STATE_FILE).get("created_at")
    if got:
        return str(got)
    try:
        st = (root / "qanat.yaml").stat()
    except OSError:
        return None
    t = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _note(root: Path) -> dict[str, Any]:
    """The `.qanat-lock` note of whoever holds this project, read without opening it.

    Opening the store to ask would take a DuckDB file's lock, so the note is read
    from where the store would write it.
    """
    import json

    from qanat.project import load
    from qanat.store import Store

    try:
        project, r = load(root)
        target = Path(project.store_url(r))
    except Exception:  # noqa: BLE001 -- a project that does not load holds nothing
        return {}
    note = (target / Store.LOCK_FILE if target.is_dir() or not target.suffix
            else target.parent / f"{target.name}{Store.LOCK_FILE}")
    try:
        got = json.loads(note.read_text())
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(got, dict):
        return {}
    pid = got.get("pid")
    try:
        os.kill(int(pid), 0)
    except (OSError, TypeError, ValueError):
        return {}           # a note left by a process that is gone holds nothing
    return got


def _who(holders: list[str]) -> str:
    """'the app and 2 MCP sessions', said once rather than listed per holder."""
    parts = ["the app"] if APP in holders else []
    n = sum(1 for h in holders if h != APP)
    if n:
        parts.append("an MCP session" if n == 1 else f"{n} MCP sessions")
    return " and ".join(parts)


# --------------------------------------------------------------------- the home
@dataclass
class Opened:
    """One project, open in this program."""

    name: str
    root: Path
    session: Any            # qanat.mcp.Session
    lock: threading.Lock = field(default_factory=threading.Lock)
    sched: Any = None


class Workspace:
    """The home folder, and which of its projects this program holds open.

    `pinned` is a program serving exactly one project with no home folder -- the
    shape every server had before this one, and still what a caller handing over
    a single project gets. It lists that project, selects it for everybody, and
    refuses to create or delete.
    """

    def __init__(self, home: Path | None = None, *, pinned: Path | None = None,
                 schedule: bool = False, workers: int = 4, serving: str | None = None,
                 doors: tuple[str, ...] = (), state: Any = None) -> None:
        self.pinned = Path(pinned).resolve() if pinned else None
        self.home = None if self.pinned else Path(home or default_home()).expanduser().resolve()
        self.schedule, self.workers = schedule, workers
        self.serving, self.doors = serving, tuple(doors)
        self._opened: dict[str, Opened] = {}
        self._selected: dict[str, str] = {}
        self._lock = threading.RLock()
        self._theme = DEFAULT_THEME
        if self.pinned and state is not None:
            # A store somebody already opened, with its scheduler: hand it over
            # rather than opening a second one, which is what StoreBusy is for.
            self._adopt(state)

    # ---- where projects are ----------------------------------------------------
    def _no_home(self) -> ProjectsError:
        return ProjectsError(
            f"this server serves one project, '{self.pinned.name}', and has no home "  # type: ignore[union-attr]
            f"folder. Start `qanat serve` without a project to manage several.")

    def _root(self, name: str) -> Path:
        if self.pinned:
            if name != self.pinned.name:
                raise self._no_home()
            return self.pinned
        if not _NAME.match(name or ""):
            raise ProjectsError(
                f"The name '{name}' is not a project name. Use lowercase letters, digits, '-' and "
                f"'_', starting with a letter or digit.")
        return self.home / name  # type: ignore[operator]

    def names(self) -> list[str]:
        if self.pinned:
            return [self.pinned.name]
        if not self.home or not self.home.is_dir():
            return []
        return sorted(p.name for p in self.home.iterdir()
                      if p.is_dir() and (p / "qanat.yaml").is_file() and _NAME.match(p.name))

    def exists(self, name: str) -> bool:
        return name in self.names()

    def _need(self, name: str) -> Path:
        root = self._root(name)
        if not (root / "qanat.yaml").is_file():
            have = self.names()
            raise ProjectsError(
                f"There is no project called '{name}'. "
                + (f"There are: {', '.join(have)}." if have else
                   "There is no project yet: create one."))
        return root

    # ---- opening ---------------------------------------------------------------
    def _adopt(self, state: Any) -> None:
        from qanat.mcp import Session

        s = Session.__new__(Session)
        s.path = str(state.root)
        s.project, s.root = state.project, state.root
        s._store = state.store
        s.state = state
        name = self.pinned.name  # type: ignore[union-attr]
        s.home_name = name
        self._opened[name] = Opened(name, Path(state.root), s, sched=state.sched)

    def open(self, name: str) -> Opened:
        """The project, open in this program and holding its writer lock."""
        with self._lock:
            if name in self._opened:
                return self._opened[name]
            root = self._need(name)
            from qanat.mcp import Session

            session = Session(str(root))
            # The folder's name is the project's name here; `project.name` in
            # the file is whatever `init` wrote and may differ.
            session.home_name = name
            try:
                session.store.take_writer_lock(serving=self.serving, doors=list(self.doors),
                                               project=session.project.name)
            except Exception:
                session.close()
                raise
            opened = Opened(name, root, session)
            if self.schedule:
                from qanat.runtime import AppState
                from qanat.scheduler import Scheduler

                sched = Scheduler(session.store, session.project, session.root,
                                  workers=self.workers)
                session.state = AppState(store=session.store, project=session.project,
                                         root=session.root, sched=sched)
                sched.start()
                opened.sched = sched
            self._opened[name] = opened
            return opened

    def is_open(self, name: str) -> bool:
        return name in self._opened

    def close_all(self) -> None:
        with self._lock:
            for o in list(self._opened.values()):
                with contextlib.suppress(Exception):
                    if o.sched:
                        o.sched.stop()
                with contextlib.suppress(Exception):
                    if (act := self._last_activity(o)) is not None:
                        stamp(o.root, act)       # a job's end; never "now"
                with contextlib.suppress(Exception):
                    o.session.close()
            self._opened.clear()

    # ---- who works where -------------------------------------------------------
    def select(self, holder: str, name: str) -> dict[str, Any]:
        """Point `holder` at a project, opening it if this program has not yet."""
        self.open(name)
        with self._lock:
            self._selected[holder] = name
        return self.describe(name, holder=holder)

    def selected(self, holder: str) -> str | None:
        with self._lock:
            got = self._selected.get(holder)
        if got is None and self.pinned:
            return self.pinned.name
        return got

    def release(self, holder: str) -> None:
        with self._lock:
            self._selected.pop(holder, None)

    def close(self, holder: str) -> dict[str, Any]:
        """Stop `holder` working in its project, and let the project go if nobody
        else is in it -- its writer lock too, so the CLI can use it again.

        **Refused while a job runs in it**, because closing the store under a
        running job is the one way to lose its execution row.
        """
        with self._lock:
            name = self._selected.get(holder)
            if name is None:
                return {"closed": None, "released": False}
            o = self._opened.get(name)
            if o is not None and self._running(o):
                jobs = ", ".join(str(r["job"]) for r in self._running(o))
                raise ProjectsError(f"Project '{name}' has a job running ({jobs}). Wait for "
                                    f"it to finish, then close it.")
            self._selected.pop(holder, None)
            released = False
            if o is not None and not self._holders(name) and not self.pinned:
                with contextlib.suppress(Exception):
                    if o.sched:
                        o.sched.stop()
                with contextlib.suppress(Exception):
                    if (act := self._last_activity(o)) is not None:
                        stamp(o.root, act)       # a job's end; never "now"
                o.session.close()
                self._opened.pop(name, None)
                released = True
        return {"closed": name, "released": released}

    def opened_for(self, holder: str) -> Opened | None:
        name = self.selected(holder)
        return None if name is None else self.open(name)

    def touch(self, holder: str) -> None:
        """Say the holder's project was just changed -- called after a write."""
        name = self.selected(holder)
        if name is not None:
            with contextlib.suppress(Exception):
                stamp(self._root(name))

    def latest(self) -> str | None:
        """The project changed last, which the app opens on."""
        rows = self.list()["projects"]
        return rows[0]["name"] if rows else None

    # ---- what each one is doing --------------------------------------------------
    @staticmethod
    def _running(o: Opened) -> list[dict[str, Any]]:
        try:
            rows = o.session.store.recent_executions(50)
        except Exception:  # noqa: BLE001 -- a status read must never fail the list
            return []
        return [{"job": r.get("definition_id"), "kind": r.get("kind"),
                 "since": str(r.get("started_at"))}
                for r in rows if r.get("status") == "running"]

    @staticmethod
    def _last_activity(o: Opened) -> str | None:
        try:
            rows = o.session.store.recent_executions(1)
        except Exception:  # noqa: BLE001
            return None
        if not rows:
            return None
        r = rows[0]
        t = r.get("ended_at") or r.get("started_at")
        if t is None or str(t) in ("NaT", "None", ""):
            return None
        try:
            return datetime.fromisoformat(str(t)).replace(tzinfo=None).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return None

    def _holders(self, name: str) -> list[str]:
        with self._lock:
            return [h for h, n in self._selected.items() if n == name]

    def describe(self, name: str, holder: str | None = None) -> dict[str, Any]:
        root = self._root(name)
        o = self._opened.get(name)
        used = last_updated(root)
        out: dict[str, Any] = {"name": name, "path": str(root)}
        if o is not None:
            act = self._last_activity(o)
            if act and (used is None or act > used):
                used = act
            running = self._running(o)
            out["status"] = "running" if running else "idle"
            out["running"] = running
        else:
            note = _note(root)
            if note:
                out["status"] = "in use"
                out["held_by"] = {k: note.get(k) for k in ("pid", "serving", "doors")
                                  if note.get(k) is not None}
            else:
                out["status"] = "closed"
            out["running"] = []
        out["created_at"] = created_at(root)
        # None until something changes in it: creating a project is not work
        # done in it, and the page says "—" rather than repeat the created date.
        out["last_updated"] = used
        # The contact this project sends to the SEC, or None. Listed so a person
        # -- or an agent -- sees which project would have a find refused.
        out["sec_contact"] = settings(root).get("user_agent")
        holders = self._holders(name)
        out["selected_here"] = holder is not None and holder in holders
        out["open_in"] = _who(holders) if holders else None
        return out

    def list(self, holder: str | None = None) -> dict[str, Any]:
        rows = [self.describe(n, holder=holder) for n in self.names()]
        # Changed last first; a project nothing has changed in sorts by when it
        # was made, so a new one sits where it was created rather than at the end.
        rows.sort(key=lambda r: (r.get("last_updated") or r.get("created_at") or "",
                                 r["name"]), reverse=True)
        return {"home": str(self.home) if self.home else None,
                "selected": self.selected(holder) if holder else None,
                # What a project with no contact of its own falls back to.
                "shell_sec_contact": os.environ.get("SEC_USER_AGENT"),
                "projects": rows}

    # ---- create and delete -------------------------------------------------------
    def create(self, name: str) -> dict[str, Any]:
        if self.pinned:
            raise self._no_home()
        root = self._root(name)
        if root.exists():
            raise ProjectsError(
                f"A project called '{name}' already exists in {self.home}. Pick another name, or select it.")
        from qanat.scaffold import write_project

        root.mkdir(parents=True)
        write_project(root, name.replace("-", "_"))
        # Created, and not yet updated: nothing has been done in it.
        path = _local(root) / STATE_FILE
        state = _read_yaml(path)
        state["created_at"] = _now()
        _write_yaml(path, state)
        return self.describe(name)

    def delete(self, name: str, holder: str, confirm: bool = False) -> dict[str, Any]:
        """Without `confirm`, what would go. With it, the folder and everything in it.

        **Refused while anybody is working in it**: the holder asking, another
        holder in this program, a job running, or another program holding its lock.
        """
        if self.pinned:
            raise self._no_home()
        root = self._need(name)
        with self._lock:
            mine = self._selected.get(holder) == name
            others = [h for h in self._holders(name) if h != holder]
        if mine:
            raise ProjectsError(
                f"Project '{name}' is the one this session is working in. Select another one "
                f"first, then delete it.")
        if others:
            who = _who(others)
            raise ProjectsError(f"Project '{name}' is in use by {who}. It cannot be deleted while "
                                f"somebody is working in it. A session lets go when it "
                                f"closes, or after an hour idle.")
        o = self._opened.get(name)
        if o is not None and self._running(o):
            jobs = ", ".join(str(r["job"]) for r in self._running(o))
            raise ProjectsError(f"Project '{name}' has a job running ({jobs}). Wait for it to finish, "
                                f"then delete.")
        if o is None:
            note = _note(root)
            if note:
                raise ProjectsError(
                    f"Project '{name}' is held by another program (pid {note.get('pid')}"
                    + (f", {note.get('serving')}" if note.get("serving") else "")
                    + "). Stop it first.")

        goes = self._what_goes(name, root)
        if not confirm:
            return {"project": name, "deleted": False, "would_delete": goes,
                    "next": "call again with confirm to delete. This cannot be undone."}
        with self._lock:
            o = self._opened.pop(name, None)
            if o is not None:
                with contextlib.suppress(Exception):
                    if o.sched:
                        o.sched.stop()
                o.session.close()
        shutil.rmtree(root)
        return {"project": name, "deleted": True, "deleted_what": goes}

    def _what_goes(self, name: str, root: Path) -> dict[str, Any]:
        tables: list[dict[str, Any]] = []
        o = self._opened.get(name)
        try:
            if o is not None:
                from qanat.catalog import tables as svc

                tables = [{"table": t["ref"], "rows": t["rows"]}
                          for t in svc(o.session.store, o.session.project)["tables"]
                          if t["rows"]]
        except Exception:  # noqa: BLE001 -- the size below still says how much
            tables = []
        filters = 0
        with contextlib.suppress(Exception):
            filters = len((_read_yaml(root / "filters.yaml").get("filters")) or {})
        size = 0
        files = 0
        for p in root.rglob("*"):
            if p.is_file():
                files += 1
                with contextlib.suppress(OSError):
                    size += p.stat().st_size
        return {"path": str(root), "files": files, "bytes": size, "tables": tables,
                "filters": filters}

    # ---- the person's settings ---------------------------------------------------
    def theme(self) -> str:
        if self.home is None:
            return self._theme
        got = _read_yaml(self.home / HOME_FILE).get("theme")
        return got if got in THEMES else DEFAULT_THEME

    def set_theme(self, theme: str) -> str:
        if theme not in THEMES:
            raise ProjectsError(f"There is no theme called '{theme}'. There are: {', '.join(THEMES)}.")
        if self.home is None:
            self._theme = theme
            return theme
        self.home.mkdir(parents=True, exist_ok=True)
        path = self.home / HOME_FILE
        got = _read_yaml(path)
        got["theme"] = theme
        _write_yaml(path, got)
        return theme

    def user_agent(self, name: str) -> dict[str, Any]:
        root = self._need(name)
        return {"project": name, "user_agent": settings(root).get("user_agent"),
                "shell": os.environ.get("SEC_USER_AGENT")}

    def set_user_agent(self, name: str, user_agent: str) -> dict[str, Any]:
        root = self._need(name)
        out = save_user_agent(root, user_agent)
        stamp(root)
        return {"project": name, **out}


__all__ = ["APP", "DEFAULT_THEME", "THEMES", "Opened", "ProjectsError", "Workspace",
           "created_at", "default_home", "last_updated", "project_env", "save_user_agent", "settings",
           "stamp"]
