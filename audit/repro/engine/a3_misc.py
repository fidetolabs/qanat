"""A3 / E1 / E3 / B3: step suicide, NaN money, retention mid-flight, run-id collision."""
import json, math, pathlib, shutil, sys, tempfile, threading
import pandas as pd
from qanat.project import load
from qanat.store import Store
from qanat.runner import run_job, run_step

SRC = pathlib.Path(sys.argv[1])

def fresh():
    tmp = pathlib.Path(tempfile.mkdtemp()) / "proj"
    shutil.copytree(SRC, tmp)
    p, root = load(tmp)
    return p, root, Store(p.store_url(root))

print("== A3: a step that calls sys.exit() ==")
p, root, s = fresh()
(root / "steps/momentum.py").write_text("import sys\ndef run(ctx):\n    sys.exit(3)\n")
try:
    r = run_job(s, p, root, "momentum")
    print("  returned:", r.status, r.error)
except BaseException as exc:
    print("  ESCAPED the runner:", type(exc).__name__, exc)
print("  runs left 'running':",
      int(s.query("SELECT count(*) c FROM _qanat_runs WHERE status='running'")["c"][0]))
print("  (a scheduler thread would die here and the console shows it running forever)")

print("\n== A3b: a step that raises KeyboardInterrupt / MemoryError ==")
for kind in ("KeyboardInterrupt", "MemoryError", "RecursionError"):
    p, root, s = fresh()
    (root / "steps/momentum.py").write_text(f"def run(ctx):\n    raise {kind}()\n")
    try:
        r = run_job(s, p, root, "momentum")
        print(f"  {kind:18} -> caught, status={r.status}")
    except BaseException as exc:
        print(f"  {kind:18} -> ESCAPED: {type(exc).__name__}")

print("\n== E1: NaN / inf weights ==")
p, root, s = fresh()
(root / "steps/momentum.py").write_text(
    "import pandas as pd, numpy as np\n"
    "def run(ctx):\n"
    "    return pd.DataFrame({'symbol':['A','B'],'weight':[np.nan, np.inf],"
    "'date':pd.to_datetime(['2024-01-01','2024-01-02'])})\n")
r = run_job(s, p, root, "momentum")
print("  step status:", r.status)
w = s.read("features.momentum")
print("  wrote:", w.to_dict("records"))
bad = {"net": float("nan"), "gross": float("inf")}
print("  json.dumps of NaN totals ->", json.dumps(bad))
print("  is that valid JSON for a browser?  strict parse:", end=" ")
try:
    json.loads(json.dumps(bad), parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c)))
    print("yes")
except ValueError as e:
    print("NO ->", e)

print("\n== E3: retention with a tiny policy ==")
p, root, s = fresh()
from qanat.retention import apply_retention, parse_duration
before = int(s.query('SELECT count(*) c FROM raw__daily_prices')["c"][0])
removed = apply_retention(s, "raw.daily_prices", "1s")
after = int(s.query('SELECT count(*) c FROM raw__daily_prices')["c"][0])
print(f"  policy '1s' on raw.daily_prices: {before} -> {after} rows (removed {removed})")
print("  '0d' parses?", end=" ")
try: print(parse_duration("0d"))
except Exception as e: print("refused:", e)
print("  raw is 'landed and never edited' -- retention deletes it anyway, no undo, no confirm")

print("\n== B3: run_id collisions (time_ns // 1000) ==")
p, root, s = fresh()
ids, errs = [], []
def start():
    try: ids.append(s.start_run("x", "step", ["a.b"]))
    except Exception as exc: errs.append(type(exc).__name__)
th = [threading.Thread(target=start) for _ in range(200)]
[t.start() for t in th]; [t.join() for t in th]
print(f"  200 concurrent start_run: {len(ids)} ok, {len(errs)} failed {set(errs)}, "
      f"{len(ids) - len(set(ids))} duplicate ids")
