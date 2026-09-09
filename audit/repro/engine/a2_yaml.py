"""A2 / B2: the project file is written non-atomically, from more than one thread."""
import pathlib, shutil, sys, tempfile, threading, time, os
import yaml
from qanat.project import load, ProjectError
from qanat.project_io import save_project

SRC = pathlib.Path(sys.argv[1])
tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
shutil.copytree(SRC, tmp)
p, root = load(tmp)
f = root / "qanat.yaml"

print("== A2: is the write atomic? ==")
import inspect
from qanat import project_io
print("  save_project uses:", [l.strip() for l in inspect.getsource(project_io.save_project).splitlines() if "write_text" in l])
size = f.stat().st_size
# simulate a crash mid-write the way write_text does it: truncate then partial write
f.write_text(f.read_text()[: size // 2])
try:
    load(tmp)
    print("  half-written file still loads (lucky)")
except Exception as exc:
    print("  half-written file ->", type(exc).__name__, str(exc).splitlines()[0][:100])
    print("  and there is no backup:", sorted(x.name for x in root.iterdir() if "yaml" in x.name))

print("\n== B2: two threads saving the project at once (console edit vs live frontier) ==")
shutil.rmtree(tmp); shutil.copytree(SRC, tmp)
p1, _ = load(tmp)
p2, _ = load(tmp)
p1.backtest.live_from = "2025-01-01"          # what Scheduler.note_frontier writes
p2.name = "renamed_by_console"                 # what a console edit writes
barrier = threading.Barrier(2)
def w(pr):
    barrier.wait()
    for _ in range(60):
        save_project(pr, root)
def_ = [threading.Thread(target=w, args=(x,)) for x in (p1, p2)]
[t.start() for t in def_]; [t.join() for t in def_]
final = yaml.safe_load(f.read_text())
print("  final name:      ", final["project"])
print("  final live_from: ", (final.get("backtest") or {}).get("live_from"))
print("  -> one writer's change is gone; last writer wins, no locking, no merge")

print("\n== B2b: reading while the other thread writes ==")
bad = []
stop = threading.Event()
def writer():
    while not stop.is_set():
        save_project(p1, root)
def reader():
    for _ in range(400):
        try: load(tmp)
        except Exception as exc: bad.append(type(exc).__name__)
tw = threading.Thread(target=writer); tw.start()
reader(); stop.set(); tw.join()
print("  torn reads seen:", len(bad), set(bad) or "none")
